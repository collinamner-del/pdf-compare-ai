#!/usr/bin/env bash
set -e

# Install tesseract system package
apt-get update
apt-get install -y tesseract-ocr

# Install Python deps
cd backend
pip install -r requirements.txt
