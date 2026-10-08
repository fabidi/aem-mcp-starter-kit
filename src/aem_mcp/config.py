"""Configuration management for AEM Content Intelligence MCP."""

import os
from pathlib import Path
from typing import Literal

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT_DIR / "data"

class Config:
    def __init__(self):
        self.mode: Literal["mock", "onprem", "cloud"] = os.getenv("AEM_MODE", "mock").lower()
        self.mock_jcr_path = Path(os.getenv("AEM_MOCK_JCR_PATH", DATA_DIR / "jcr_mock_store.json"))
        self.property_master_db = Path(os.getenv("PROPERTY_MASTER_DB", DATA_DIR / "property_master.sqlite"))
        
        # Live AEM Configuration
        self.aem_url = os.getenv("AEM_URL", "http://localhost:4502").rstrip("/")
        self.link_base_url = os.getenv("AEM_LINK_BASE_URL", self.aem_url).rstrip("/")
        self.username = os.getenv("AEM_USERNAME", "admin")
        self.password = os.getenv("AEM_PASSWORD", "admin")
        
        # AEM Cloud Service (IMS OAuth)
        self.ims_client_id = os.getenv("AEM_IMS_CLIENT_ID", "")
        self.ims_client_secret = os.getenv("AEM_IMS_CLIENT_SECRET", "")
        self.ims_org_id = os.getenv("AEM_IMS_ORG_ID", "")

    def is_mock(self) -> bool:
        return self.mode == "mock"

CONFIG = Config()
