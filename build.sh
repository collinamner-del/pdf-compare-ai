cat > build.sh << 'EOF'
#!/usr/bin/env bash
set -e
apt-get update
apt-get install -y tesseract-ocr
cd backend
pip install -r requirements.txt
EOF
