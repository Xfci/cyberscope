# CYBERSCOPE 👁️‍🗨️

**CYBERSCOPE** is a sleek, web-based Open Source Intelligence (OSINT) and reconnaissance tool designed to extract, classify, and analyze domains and images from any given target URL or local HTML file. 

Featuring a built-in cyberpunk-styled web interface, it uses Server-Sent Events (SSE) to stream real-time scan results. Furthermore, it leverages **Optical Character Recognition (OCR)** to find hidden domains embedded directly inside images.

## ✨ Features

* **🌐 Domain Classification:** Automatically extracts and categorizes hosts into `PRIMARY`, `SUBDOMAIN`, `CDN`, `TRACKER`, and `EXTERNAL` using Public Suffix List (PSL) logic and regex patterns.
* **🖼️ Image Reconnaissance:** Discovers all image assets on a page, flags missing `alt` attributes, and highlights images loaded from external sources.
* **🔍 OCR Domain Extraction:** Downloads images in-memory and scans them using Tesseract OCR to extract domains written in image text (e.g., watermarks, banners, contact info).
* **⚡ Real-Time Streaming:** Uses Server-Sent Events (SSE) to deliver terminal-like live updates to the browser without refreshing.
* **💻 Built-in UI:** Single-file architecture includes a fully styled, responsive, dark-mode web interface.

### 1. Install Tesseract OCR
* **Debian/Ubuntu:** `sudo apt-get install tesseract-ocr`
* **macOS:** `brew install tesseract`
* **Windows:** Download the installer from [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki) (Note: You may need to uncomment and update the `tesseract_cmd` path in `app.py`).

### 2. Install Python Dependencies
```bash
pip install flask requests beautifulsoup4 pytesseract Pillow
