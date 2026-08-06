"""Paket collector. Memuat .env satu kali agar semua scanner/storage konsisten."""

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))