#!/bin/bash
# Build script for Render.com deployment
# Playwright is optional - scraper works without it for non-JS sites

echo "📦 Installing core Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Try to install Playwright (optional - may fail on some Python versions)
echo "🎭 Attempting Playwright installation (optional)..."
if pip install playwright==1.41.0 2>/dev/null; then
    echo "✅ Playwright installed, installing Chromium..."
    python -m playwright install chromium 2>/dev/null || echo "⚠️ Chromium install failed - JS rendering disabled"
else
    echo "⚠️ Playwright not available on this Python version - JS rendering disabled"
    echo "   Scraper will work for static HTML sites"
fi

echo "✅ Build complete!"
