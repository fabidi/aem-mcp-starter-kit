"""
Dataset Materialization, In-Memory Analytics, and Export Engine.

Enables the AEM MCP server to handle large query results without overwhelming
the model context window:
1. Materializes paginated JCR hits into SQLite-backed datasets (ds_<12hex>).
2. Runs server-side analysis (count, group_by, missing, filter).
3. Cross-system matching (AEM content vs Property Master).
4. Dependency-free XLSX and CSV artifact generation with local HTTP streaming.
"""

import csv
import io
import json
import re
import sqlite3
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from aem_mcp.config import ROOT_DIR

DATASETS_DIR = ROOT_DIR / "datasets"
_DATASET_ID = re.compile(r"^ds_[0-9a-f]{12}$")
_EXPORT_ID = re.compile(r"^export_[0-9a-f]{12}$")
_DATASET_TTL_SECONDS = 24 * 60 * 60

_ARTIFACT_TTL_SECONDS = 24 * 60 * 60
_ARTIFACTS: dict[str, tuple[Path, str, float]] = {}
_ARTIFACTS_LOCK = threading.Lock()
_ARTIFACT_SERVER: ThreadingHTTPServer | None = None
_ARTIFACT_BASE_URL = ""


def cleanup_expired_datasets() -> None:
    """Remove stale datasets past their TTL."""
    if not DATASETS_DIR.exists():
        return
    cutoff = datetime.now(timezone.utc).timestamp() - _DATASET_TTL_SECONDS
    for path in DATASETS_DIR.iterdir():
        if path.is_file() and path.name.startswith("ds_"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except FileNotFoundError:
                pass


def _dataset_connection(dataset_id: str) -> sqlite3.Connection:
    if not _DATASET_ID.fullmatch(dataset_id):
        raise ValueError(f"Invalid dataset ID: {dataset_id}")
    DATASETS_DIR.mkdir(exist_ok=True, parents=True)
    conn = sqlite3.connect(DATASETS_DIR / f"{dataset_id}.sqlite")
    conn.row_factory = sqlite3.Row
    return conn


class StreamingDatasetBuilder:
    """
    Incremental dataset writer that streams batches into an on-disk SQLite database.
    Maintains a bounded memory footprint O(batch_size) and configures SQLite WAL mode
    with a bounded cache ceiling (~8MB).
    """
    def __init__(self, metadata: dict[str, Any] | None = None):
        self.dataset_id = "ds_" + uuid.uuid4().hex[:12]
        self.conn = _dataset_connection(self.dataset_id)
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA synchronous = NORMAL;")
        self.conn.execute("PRAGMA cache_size = -8000;")
        self.conn.executescript(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "CREATE TABLE rows (row_number INTEGER PRIMARY KEY, data TEXT NOT NULL);"
        )
        self.row_count = 0
        self.metadata = {
            "dataset_id": self.dataset_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **(metadata or {})
        }
        self._closed = False

    def append_batch(self, rows: list[dict[str, Any]]) -> int:
        """Append a batch of rows to the SQLite table."""
        if not rows or self._closed:
            return 0
        start_idx = self.row_count
        self.conn.executemany(
            "INSERT INTO rows (row_number, data) VALUES (?, ?)",
            ((start_idx + i, json.dumps(r, ensure_ascii=False)) for i, r in enumerate(rows))
        )
        self.row_count += len(rows)
        self.conn.commit()
        return len(rows)

    def close(self, extra_metadata: dict[str, Any] | None = None) -> str:
        """Finalize metadata and close the connection."""
        if self._closed:
            return self.dataset_id
        try:
            if extra_metadata:
                self.metadata.update(extra_metadata)
            self.metadata["row_count"] = self.row_count
            self.conn.executemany(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ((k, json.dumps(v, ensure_ascii=False)) for k, v in self.metadata.items())
            )
            self.conn.commit()
        finally:
            self.conn.close()
            self._closed = True
        return self.dataset_id

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            try:
                self.conn.close()
                self._closed = True
            except Exception:
                pass
        else:
            self.close()


def materialize_dataset(rows: list[dict[str, Any]], metadata: dict[str, Any]) -> str:
    """Store rows into a dedicated SQLite dataset table and return dataset_id."""
    with StreamingDatasetBuilder(metadata=metadata) as builder:
        builder.append_batch(rows)
    return builder.dataset_id


def get_dataset_metadata(dataset_id: str) -> dict[str, Any]:
    conn = _dataset_connection(dataset_id)
    try:
        records = conn.execute("SELECT key, value FROM metadata").fetchall()
    finally:
        conn.close()
    if not records:
        raise ValueError(f"Dataset {dataset_id} not found.")
    return {rec["key"]: json.loads(rec["value"]) for rec in records}


def get_dataset_rows(dataset_id: str, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    offset = max(int(offset), 0)
    limit = min(max(int(limit), 1), 100000)
    conn = _dataset_connection(dataset_id)
    try:
        records = conn.execute(
            "SELECT data FROM rows WHERE row_number >= ? ORDER BY row_number LIMIT ?",
            (offset, limit)
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(rec["data"]) for rec in records]



def _build_json_path(field: str) -> str:
    """Build safe SQLite JSON1 path escaping double quotes."""
    escaped = field.replace('"', '""')
    return f'$."{escaped}"'


def _build_sql_filter_clause(fld_path: str, operator: str, value: Any) -> tuple[str, list[Any]]:
    """Build parameterized SQL WHERE condition for SQLite JSON1 query."""
    op = operator.lower().strip()
    val_str = str(value)

    if op in ("equals", "==", "="):
        return f"LOWER(CAST(json_extract(data, '{fld_path}') AS TEXT)) = LOWER(?)", [val_str]
    elif op in ("unequals", "!=", "<>"):
        return f"LOWER(CAST(json_extract(data, '{fld_path}') AS TEXT)) != LOWER(?)", [val_str]
    elif op == "contains":
        return f"LOWER(CAST(json_extract(data, '{fld_path}') AS TEXT)) LIKE ?", [f"%{val_str.lower()}%"]
    elif op == "starts_with":
        return f"LOWER(CAST(json_extract(data, '{fld_path}') AS TEXT)) LIKE ?", [f"{val_str.lower()}%"]
    elif op == "ends_with":
        return f"LOWER(CAST(json_extract(data, '{fld_path}') AS TEXT)) LIKE ?", [f"%{val_str.lower()}"]
    elif op == "like":
        return f"CAST(json_extract(data, '{fld_path}') AS TEXT) LIKE ?", [val_str]
    elif op in ("exists", "present"):
        return f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != ''", []
    elif op in ("not", "missing", "empty"):
        return f"json_extract(data, '{fld_path}') IS NULL OR json_extract(data, '{fld_path}') = ''", []
    elif op in ("gt", ">"):
        try:
            return f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' AND CAST(json_extract(data, '{fld_path}') AS REAL) > ?", [float(val_str)]
        except ValueError:
            return f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' AND json_extract(data, '{fld_path}') > ?", [val_str]
    elif op in ("gte", ">="):
        try:
            return f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' AND CAST(json_extract(data, '{fld_path}') AS REAL) >= ?", [float(val_str)]
        except ValueError:
            return f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' AND json_extract(data, '{fld_path}') >= ?", [val_str]
    elif op in ("lt", "<"):
        try:
            return f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' AND CAST(json_extract(data, '{fld_path}') AS REAL) < ?", [float(val_str)]
        except ValueError:
            return f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' AND json_extract(data, '{fld_path}') < ?", [val_str]
    elif op in ("lte", "<="):
        try:
            return f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' AND CAST(json_extract(data, '{fld_path}') AS REAL) <= ?", [float(val_str)]
        except ValueError:
            return f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' AND json_extract(data, '{fld_path}') <= ?", [val_str]
    elif op == "between":
        if "," in val_str:
            low, high = val_str.split(",", 1)
            low, high = low.strip(), high.strip()
            try:
                return (
                    f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' AND "
                    f"CAST(json_extract(data, '{fld_path}') AS REAL) BETWEEN ? AND ?",
                    [float(low), float(high)]
                )
            except ValueError:
                return (
                    f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' AND "
                    f"json_extract(data, '{fld_path}') BETWEEN ? AND ?",
                    [low, high]
                )
        else:
            return f"json_extract(data, '{fld_path}') = ?", [val_str]
    elif op in ("after", "date_after"):
        return f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' AND json_extract(data, '{fld_path}') > ?", [val_str]
    elif op in ("before", "date_before"):
        return f"json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' AND json_extract(data, '{fld_path}') < ?", [val_str]
    else:
        return f"LOWER(CAST(json_extract(data, '{fld_path}') AS TEXT)) = LOWER(?)", [val_str]


def analyze_dataset(
    dataset_id: str,
    operation: str,
    field: str = "",
    value: str = "",
    other_field: str = "",
    operator: str = "equals"
) -> dict[str, Any]:
    """
    Execute server-side aggregation and analytical operations.
    Leverages SQLite's native C-engine JSON1 functions (json_extract) for
    sub-millisecond aggregations without row count ceilings or in-memory Python loops.
    """
    conn = _dataset_connection(dataset_id)
    try:
        total_rows_res = conn.execute("SELECT COUNT(*) FROM rows").fetchone()
        total_rows = total_rows_res[0] if total_rows_res else 0
        if total_rows == 0:
            return {"operation": operation, "dataset_id": dataset_id, "total_rows": 0}

        op = operation.lower().strip()
        fld_path = _build_json_path(field) if field else ""

        if op == "count":
            if not field:
                return {"operation": "count", "dataset_id": dataset_id, "total_rows": total_rows}
            clause, params = _build_sql_filter_clause(fld_path, operator, value)
            matching = conn.execute(f"SELECT COUNT(*) FROM rows WHERE {clause}", params).fetchone()[0]
            return {
                "operation": "count",
                "field": field,
                "operator": operator,
                "value": value,
                "matching_rows": matching,
                "total_rows": total_rows,
                "percentage": round((matching / total_rows * 100), 2) if total_rows else 0
            }

        elif op == "missing":
            sql = f"""
                SELECT 
                    SUM(CASE WHEN json_extract(data, '{fld_path}') IS NULL OR json_extract(data, '{fld_path}') = '' THEN 1 ELSE 0 END)
                FROM rows
            """
            missing_count = conn.execute(sql).fetchone()[0] or 0
            present_count = total_rows - missing_count
            return {
                "operation": "missing",
                "field": field,
                "missing_count": missing_count,
                "present_count": present_count,
                "total_rows": total_rows,
                "completeness_pct": round((present_count / total_rows * 100), 2) if total_rows else 0
            }

        elif op == "group_by":
            sql = f"""
                SELECT 
                    COALESCE(json_extract(data, '{fld_path}'), '<MISSING>') as val,
                    COUNT(*) as cnt
                FROM rows
                GROUP BY val
                ORDER BY cnt DESC
                LIMIT 100
            """
            records = conn.execute(sql).fetchall()
            sorted_counts = {str(r[0]): r[1] for r in records}
            return {
                "operation": "group_by",
                "field": field,
                "total_rows": total_rows,
                "distinct_values": len(sorted_counts),
                "groups": sorted_counts
            }

        elif op in ("stats", "numeric_summary", "avg", "min", "max", "sum"):
            sql = f"""
                SELECT 
                    COUNT(CASE WHEN json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != '' THEN 1 END) as cnt,
                    MIN(CAST(json_extract(data, '{fld_path}') AS REAL)) as min_val,
                    MAX(CAST(json_extract(data, '{fld_path}') AS REAL)) as max_val,
                    ROUND(AVG(CAST(json_extract(data, '{fld_path}') AS REAL)), 2) as avg_val,
                    SUM(CAST(json_extract(data, '{fld_path}') AS REAL)) as sum_val
                FROM rows
                WHERE json_extract(data, '{fld_path}') IS NOT NULL AND json_extract(data, '{fld_path}') != ''
            """
            row = conn.execute(sql).fetchone()
            cnt, min_v, max_v, avg_v, sum_v = row if row else (0, None, None, None, None)
            res = {
                "operation": op,
                "field": field,
                "total_rows": total_rows,
                "numeric_rows": cnt,
                "min": min_v,
                "max": max_v,
                "avg": avg_v,
                "sum": sum_v
            }
            if op in ("avg", "min", "max", "sum"):
                res["result"] = res[op]
            return res

        elif op == "filter":
            clause, params = _build_sql_filter_clause(fld_path, operator, value)
            matched_count = conn.execute(f"SELECT COUNT(*) FROM rows WHERE {clause}", params).fetchone()[0]
            sample_records = conn.execute(f"SELECT data FROM rows WHERE {clause} LIMIT 20", params).fetchall()
            sample = [json.loads(r[0]) for r in sample_records]
            return {
                "operation": "filter",
                "field": field,
                "operator": operator,
                "value": value,
                "matched_count": matched_count,
                "sample": sample
            }

        else:
            raise ValueError(f"Unsupported analysis operation: '{operation}'. Use 'count', 'missing', 'group_by', 'stats', 'avg', 'min', 'max', 'sum', or 'filter'.")

    finally:
        conn.close()


def match_datasets(
    left_dataset_id: str,
    right_dataset_id: str,
    left_field: str,
    right_field: str,
    match_type: str = "exact"
) -> dict[str, Any]:
    """Join two datasets on a key and identify matches, orphans, and discrepancies."""
    left_rows = get_dataset_rows(left_dataset_id, offset=0, limit=100000)
    right_rows = get_dataset_rows(right_dataset_id, offset=0, limit=100000)

    right_lookup: dict[str, list[dict[str, Any]]] = {}
    for r in right_rows:
        val = str(r.get(right_field, "")).strip().lower()
        if val:
            right_lookup.setdefault(val, []).append(r)

    reconciled = []
    matched_right_keys = set()

    for l_row in left_rows:
        l_key = str(l_row.get(left_field, "")).strip().lower()
        r_matches = right_lookup.get(l_key, [])

        if r_matches:
            matched_right_keys.add(l_key)
            for r_row in r_matches:
                # Detect discrepancies across common field names
                discrepancies = []
                for common_col in set(l_row.keys()) & set(r_row.keys()):
                    if common_col in {left_field, right_field}:
                        continue
                    l_val, r_val = str(l_row.get(common_col)), str(r_row.get(common_col))
                    if l_val.lower() != r_val.lower():
                        discrepancies.append(f"{common_col}: left='{l_val}' vs right='{r_val}'")

                entry = {
                    "match_status": "MATCHED",
                    "match_key": l_key,
                    "discrepancies": discrepancies,
                    "has_discrepancy": len(discrepancies) > 0,
                    **{f"left_{k}": v for k, v in l_row.items()},
                    **{f"right_{k}": v for k, v in r_row.items()},
                }
                reconciled.append(entry)
        else:
            reconciled.append({
                "match_status": "LEFT_ONLY",
                "match_key": l_key,
                "discrepancies": ["No matching right record found"],
                "has_discrepancy": True,
                **{f"left_{k}": v for k, v in l_row.items()}
            })

    # Unmatched right records
    for r_key, r_rows in right_lookup.items():
        if r_key not in matched_right_keys:
            for r_row in r_rows:
                reconciled.append({
                    "match_status": "RIGHT_ONLY",
                    "match_key": r_key,
                    "discrepancies": ["No matching left record found"],
                    "has_discrepancy": True,
                    **{f"right_{k}": v for k, v in r_row.items()}
                })

    new_ds_id = materialize_dataset(
        reconciled,
        {
            "type": "reconciliation",
            "left_dataset_id": left_dataset_id,
            "right_dataset_id": right_dataset_id,
            "left_field": left_field,
            "right_field": right_field,
        }
    )

    matched_count = sum(1 for r in reconciled if r["match_status"] == "MATCHED")
    discrepancy_count = sum(1 for r in reconciled if r.get("has_discrepancy"))

    return {
        "dataset_id": new_ds_id,
        "total_rows": len(reconciled),
        "matched_count": matched_count,
        "discrepancy_count": discrepancy_count,
        "left_only_count": sum(1 for r in reconciled if r["match_status"] == "LEFT_ONLY"),
        "right_only_count": sum(1 for r in reconciled if r["match_status"] == "RIGHT_ONLY"),
        "sample": reconciled[:10]
    }


def discard_dataset(dataset_id: str) -> bool:
    import gc
    import time
    gc.collect()
    if not _DATASET_ID.fullmatch(dataset_id):
        raise ValueError(f"Invalid dataset ID: {dataset_id}")
    target = DATASETS_DIR / f"{dataset_id}.sqlite"
    if target.exists():
        try:
            target.unlink()
            return True
        except PermissionError:
            gc.collect()
            time.sleep(0.05)
            target.unlink(missing_ok=True)
            return True
    return False



# --- Dependency-free XLSX & CSV Export Engine ---

def _xlsx_cell(value: Any, row: int, col: int) -> str:
    col_str = ""
    num = col
    while num:
        num, rem = divmod(num - 1, 26)
        col_str = chr(65 + rem) + col_str
    ref = f"{col_str}{row}"
    val_str = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or "")
    escaped = (
        val_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )
    return f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'


def _xlsx_bytes(rows: list[dict[str, Any]], fields: list[str]) -> bytes:
    """Create a clean, dependency-free XLSX workbook using Python's built-in zipfile."""
    xml_rows = []
    # Header row
    xml_rows.append(
        '<row r="1">' + "".join(_xlsx_cell(f, 1, idx) for idx, f in enumerate(fields, 1)) + "</row>"
    )
    # Data rows
    for row_idx, row in enumerate(rows, 2):
        cells = "".join(_xlsx_cell(row.get(f, ""), row_idx, col_idx) for col_idx, f in enumerate(fields, 1))
        xml_rows.append(f'<row r="{row_idx}">{cells}</row>')

    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Audit Report" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    package_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as arc:
        arc.writestr("[Content_Types].xml", content_types)
        arc.writestr("_rels/.rels", package_rels)
        arc.writestr("xl/workbook.xml", workbook)
        arc.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        arc.writestr("xl/worksheets/sheet1.xml", sheet)
    return out.getvalue()


class _ArtifactHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        token = self.path.removeprefix("/artifacts/").split("?", 1)[0]
        with _ARTIFACTS_LOCK:
            artifact = _ARTIFACTS.get(token)
            if artifact and artifact[2] < datetime.now(timezone.utc).timestamp():
                _ARTIFACTS.pop(token, None)
                artifact = None
        if not artifact or not artifact[0].is_file():
            self.send_error(404, "Artifact Not Found or Expired")
            return
        path, filename, _ = artifact
        data = path.read_bytes()
        self.send_response(200)
        content_type = {
            ".csv": "text/csv; charset=utf-8",
            ".json": "application/json",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }.get(path.suffix.lower(), "application/octet-stream")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, _format: str, *_args) -> None:
        return


def start_artifact_server() -> str:
    global _ARTIFACT_SERVER, _ARTIFACT_BASE_URL
    if _ARTIFACT_SERVER is None:
        _ARTIFACT_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _ArtifactHandler)
        port = _ARTIFACT_SERVER.server_address[1]
        _ARTIFACT_BASE_URL = f"http://localhost:{port}"
        threading.Thread(target=_ARTIFACT_SERVER.serve_forever, daemon=True, name="aem-artifacts").start()
    return _ARTIFACT_BASE_URL


def export_dataset(dataset_id: str, fmt: str = "csv") -> dict[str, Any]:
    """Generate a downloadable export artifact (CSV or XLSX)."""
    rows = get_dataset_rows(dataset_id, offset=0, limit=100000)
    if not rows:
        raise ValueError(f"Dataset {dataset_id} has no rows to export.")

    fields = sorted({k for r in rows for k in r.keys()})
    export_id = "export_" + uuid.uuid4().hex[:12]
    export_dir = DATASETS_DIR / "exports"
    export_dir.mkdir(exist_ok=True, parents=True)

    ext = ".xlsx" if fmt.lower() == "xlsx" else ".csv"
    filename = f"{dataset_id}_{export_id}{ext}"
    export_path = export_dir / filename

    if ext == ".xlsx":
        export_path.write_bytes(_xlsx_bytes(rows, fields))
    else:
        with open(export_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()})

    token = "artifact_" + uuid.uuid4().hex
    with _ARTIFACTS_LOCK:
        _ARTIFACTS[token] = (
            export_path,
            filename,
            datetime.now(timezone.utc).timestamp() + _ARTIFACT_TTL_SECONDS
        )

    base_url = start_artifact_server()
    download_url = f"{base_url}/artifacts/{token}"

    return {
        "dataset_id": dataset_id,
        "export_id": export_id,
        "format": ext.lstrip("."),
        "row_count": len(rows),
        "fields": fields,
        "file_name": filename,
        "download_url": download_url,
        "file_path": str(export_path)
    }
