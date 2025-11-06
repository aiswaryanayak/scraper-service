from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import json

app = Flask(__name__)
CORS(app)  # Allow requests from your Next.js app

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "service": "scraper-api"})

@app.route('/scrape', methods=['POST'])
def scrape():
    try:
        data = request.get_json()
        url = data.get('url')
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        # Fetch the webpage
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Parse HTML
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract structured data
        scraped_data = {
            "url": url,
            "company_name": extract_company_name(soup, url),
            "title": extract_title(soup),
            "description": extract_description(soup),
            "pricing": extract_pricing(soup),
            "features": extract_features(soup),
            "team": extract_team(soup),
            "metrics": extract_metrics(soup),
            "contact": extract_contact(soup),
            "social_links": extract_social_links(soup),
            "raw_text": extract_clean_text(soup)
        }
        
        return jsonify({
            "success": True,
            "data": scraped_data
        })
        
    except requests.RequestException as e:
        return jsonify({"error": f"Failed to fetch URL: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": f"Scraping failed: {str(e)}"}), 500


def extract_company_name(soup, url):
    """Extract company name from various sources"""
    # Try meta tags
    og_site_name = soup.find('meta', property='og:site_name')
    if og_site_name:
        return og_site_name.get('content', '').strip()
    
    # Try title
    title = soup.find('title')
    if title:
        name = title.string.split('|')[0].split('-')[0].strip()
        return name
    
    # Extract from URL
    domain = url.split('/')[2].replace('www.', '').split('.')[0]
    return domain.capitalize()


def extract_title(soup):
    """Extract page title"""
    title = soup.find('title')
    return title.string.strip() if title else ""


def extract_description(soup):
    """Extract meta description"""
    meta_desc = soup.find('meta', attrs={'name': 'description'})
    if meta_desc:
        return meta_desc.get('content', '').strip()
    
    og_desc = soup.find('meta', property='og:description')
    if og_desc:
        return og_desc.get('content', '').strip()
    
    return ""


def extract_pricing(soup):
    """Extract pricing information"""
    pricing = []
    
    # Look for common pricing patterns
    price_keywords = ['price', 'pricing', 'plan', 'cost', '$', '€', '£']
    
    # Find elements containing pricing
    for keyword in price_keywords:
        elements = soup.find_all(string=re.compile(keyword, re.I))
        for elem in elements[:10]:  # Limit to avoid too much data
            parent = elem.parent
            text = parent.get_text(strip=True)
            
            # Extract price numbers
            prices = re.findall(r'[\$€£]\s*\d+(?:,\d{3})*(?:\.\d{2})?', text)
            if prices:
                pricing.extend(prices)
    
    return list(set(pricing))[:10]  # Remove duplicates, limit to 10


def extract_features(soup):
    """Extract product/service features"""
    features = []
    
    # Look for lists (ul, ol)
    lists = soup.find_all(['ul', 'ol'], limit=5)
    for lst in lists:
        items = lst.find_all('li')
        for item in items[:10]:
            text = item.get_text(strip=True)
            if 20 < len(text) < 200:  # Reasonable feature length
                features.append(text)
    
    return features[:20]  # Limit features


def extract_team(soup):
    """Extract team member information"""
    team = []
    
    # Look for common team patterns
    team_keywords = ['team', 'founder', 'ceo', 'cto', 'leadership', 'about us']
    
    for keyword in team_keywords:
        sections = soup.find_all(string=re.compile(keyword, re.I))
        for section in sections[:3]:
            parent = section.find_parent(['div', 'section'])
            if parent:
                # Look for names (capitalized words)
                text = parent.get_text()
                names = re.findall(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+\b', text)
                team.extend(names[:5])
    
    return list(set(team))[:10]


def extract_metrics(soup):
    """Extract key metrics and numbers"""
    metrics = {}
    
    # Look for numbers with context
    number_patterns = [
        (r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:million|M)\s+(?:users|customers)', 'users'),
        (r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:billion|B)\s+(?:valuation|revenue)', 'valuation'),
        (r'(\d+)\+?\s*(?:countries|nations)', 'countries'),
        (r'(\d+)%\s*(?:growth|increase)', 'growth_rate'),
        (r'\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:M|million)', 'funding')
    ]
    
    text = soup.get_text()
    
    for pattern, key in number_patterns:
        matches = re.findall(pattern, text, re.I)
        if matches:
            metrics[key] = matches[0]
    
    return metrics


def extract_contact(soup):
    """Extract contact information"""
    contact = {}
    
    # Email
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', soup.get_text())
    if emails:
        contact['email'] = emails[0]
    
    # Phone
    phones = re.findall(r'[\+]?[(]?[0-9]{1,3}[)]?[-\s\.]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,9}', soup.get_text())
    if phones:
        contact['phone'] = phones[0]
    
    return contact


def extract_social_links(soup):
    """Extract social media links"""
    social = {}
    
    social_patterns = {
        'linkedin': r'linkedin\.com/company/([^/\s"\']+)',
        'twitter': r'twitter\.com/([^/\s"\']+)',
        'facebook': r'facebook\.com/([^/\s"\']+)',
        'instagram': r'instagram\.com/([^/\s"\']+)'
    }
    
    html = str(soup)
    
    for platform, pattern in social_patterns.items():
        matches = re.findall(pattern, html, re.I)
        if matches:
            social[platform] = matches[0]
    
    return social


def extract_clean_text(soup):
    """Extract clean text content (fallback)"""
    # Remove script and style elements
    for script in soup(['script', 'style', 'nav', 'footer', 'header']):
        script.decompose()
    
    # Get text
    text = soup.get_text(separator=' ', strip=True)
    
    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text)
    
    # Limit to 5000 characters
    return text[:5000]


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
