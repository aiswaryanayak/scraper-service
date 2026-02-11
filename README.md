# Scraper Service for AI Fundraising Support

Professional web scraping API service that extracts structured data from startup websites.

## Features

- ✅ Company name extraction
- ✅ Pricing information
- ✅ Product features
- ✅ Team members
- ✅ Key metrics (users, funding, growth)
- ✅ Contact information
- ✅ Social media links
- ✅ Clean text content
- ✅ **Playwright JS rendering** - handles React/Vue/Angular sites

## Deploy to Render

1. **Create Render Account:**
   - Go to https://render.com
   - Sign up for free

2. **Create New Web Service:**
   - Click "New +" → "Web Service"
   - Connect this GitHub repo OR upload files directly
   
3. **Configure:**
   - **Name:** `ai-fundraising-scraper`
   - **Environment:** Python 3
   - **Build Command:** `chmod +x build.sh && ./build.sh`
   - **Start Command:** `gunicorn app:app`
   - **Plan:** Free (or Starter for better performance)
   
   > ⚠️ **Important:** The `runtime.txt` file pins Python to 3.11.7 for Playwright compatibility. The build command installs Chromium browser for JS-heavy sites.

4. **Files Required:**
   - `runtime.txt` - Specifies Python 3.11.7 (required for greenlet/Playwright)
   - `build.sh` - Installs dependencies + attempts Playwright (optional)
   - `requirements.txt` - Core Python packages
   - `Procfile` - Gunicorn start command
   
   > **Note:** Playwright for JS rendering is optional. If it fails to install (Python version incompatibility), the scraper still works for static HTML sites. For JS-heavy sites, you may need to use Render's Docker deployment with a custom Python version.
   
5. **Deploy:**
   - Click "Create Web Service"
   - Wait 3-5 minutes for deployment (Playwright download takes time)
   - Get your URL: `https://ai-fundraising-scraper.onrender.com`

## Alternative: Docker Deployment (Recommended for JS Rendering)

If the native Python build fails due to greenlet/Playwright compatibility issues:

1. **Create Web Service from Docker:**
   - Click "New +" → "Web Service"
   - Select "Docker" as the environment
   - The `Dockerfile` in the repo will be auto-detected
   
2. **Configure:**
   - **Name:** `ai-fundraising-scraper`
   - **Instance Type:** Starter ($7/mo) or higher (Free tier may timeout)
   - Render will build from `Dockerfile` automatically

3. **Benefits:**
   - Guaranteed Python 3.11 with Playwright support
   - Full JS rendering for React/Vue/Angular sites
   - More reliable for production use

## API Endpoints

### Health Check
```bash
GET /health
```

### Scrape URL
```bash
POST /scrape
Content-Type: application/json

{
  "url": "https://example.com"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "company_name": "Example Co",
    "title": "Example - Product Title",
    "description": "Company description...",
    "pricing": ["$99", "$299"],
    "features": ["Feature 1", "Feature 2"],
    "team": ["John Doe", "Jane Smith"],
    "metrics": {
      "users": "1M",
      "funding": "5"
    },
    "contact": {
      "email": "hello@example.com"
    },
    "social_links": {
      "linkedin": "example-co",
      "twitter": "exampleco"
    },
    "raw_text": "Clean text content..."
  }
}
```

## Local Testing

```bash
pip install -r requirements.txt
python app.py
```

Then test:
```bash
curl -X POST http://localhost:5000/scrape \
  -H "Content-Type: application/json" \
  -d '{"url": "https://modalyst.co"}'
```

## Integration with Next.js

Update your `/api/extract` route to call this service:

```typescript
const response = await fetch('https://your-scraper.onrender.com/scrape', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ url: startupUrl })
});

const scrapedData = await response.json();
// Send to Gemini for analysis
```

## Security

- CORS enabled for your Next.js domain
- Rate limiting recommended (add if needed)
- No API key required (can add if needed)

## Troubleshooting

- **503 errors:** Render free tier spins down after 15 min inactivity. First request may take 30-60s to wake up.
- **Timeout:** Some websites may take longer to scrape. Increase timeout in code if needed.
- **Blocked:** Some sites block scrapers. Use rotating proxies if needed (premium feature).
