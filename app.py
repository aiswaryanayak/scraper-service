from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import json
import time
import random
import hashlib
from datetime import datetime, timedelta
import asyncio
import os

# ============================================
# PLAYWRIGHT HEADLESS BROWSER SUPPORT
# For JavaScript-heavy websites like React/Vue/Angular
# ============================================

PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
    print("✅ Playwright available for JS rendering")
except ImportError:
    print("⚠️ Playwright not available - JS sites may have limited content")

# Threshold for triggering headless browser rendering
MIN_RAW_TEXT_LENGTH = 1000  # If less than this, try Playwright

async def render_with_playwright_async(url, timeout=30000):
    """
    Render a JavaScript-heavy page using Playwright headless browser.
    Returns the fully rendered HTML after JS execution.
    """
    try:
        async with async_playwright() as p:
            # Use Chromium for best compatibility
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-gpu',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-setuid-sandbox'
                ]
            )
            
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080}
            )
            
            page = await context.new_page()
            
            # Navigate and wait for network to be idle
            await page.goto(url, wait_until='networkidle', timeout=timeout)
            
            # Additional wait for dynamic content
            await page.wait_for_timeout(2000)
            
            # Get rendered HTML
            rendered_html = await page.content()
            
            await browser.close()
            
            print(f"🎭 Playwright rendered HTML length: {len(rendered_html)}")
            return rendered_html
            
    except Exception as e:
        print(f"⚠️ Playwright rendering failed: {str(e)}")
        return None

def render_with_playwright(url):
    """
    Synchronous wrapper for Playwright rendering.
    Creates a new event loop to run the async function.
    """
    if not PLAYWRIGHT_AVAILABLE:
        print("⚠️ Playwright not installed - skipping JS rendering")
        return None
    
    try:
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(render_with_playwright_async(url))
        finally:
            loop.close()
    except Exception as e:
        print(f"⚠️ Playwright sync wrapper failed: {str(e)}")
        return None

app = Flask(__name__)
CORS(app)  # Allow requests from your Next.js app

# ============================================
# PHASE 4: CACHING LAYER
# ============================================

# In-memory cache (use Redis for production)
scrape_cache = {}
CACHE_TTL_SECONDS = 30 * 60  # 30 minutes

def get_cache_key(url):
    """Generate cache key from URL"""
    normalized = url.lower().strip().rstrip('/')
    return hashlib.md5(normalized.encode()).hexdigest()

def get_cached_scrape(url):
    """Get cached scrape result if still valid"""
    key = get_cache_key(url)
    if key in scrape_cache:
        entry = scrape_cache[key]
        if datetime.now() < entry['expires']:
            print(f"📦 CACHE HIT for {url}")
            return entry['data']
        else:
            del scrape_cache[key]
    return None

def set_cached_scrape(url, data):
    """Cache scrape result"""
    key = get_cache_key(url)
    scrape_cache[key] = {
        'data': data,
        'expires': datetime.now() + timedelta(seconds=CACHE_TTL_SECONDS),
        'url': url
    }
    print(f"📦 CACHED scrape for {url}")

def clear_cache(url=None):
    """Clear cache for specific URL or all"""
    global scrape_cache
    if url:
        key = get_cache_key(url)
        if key in scrape_cache:
            del scrape_cache[key]
    else:
        scrape_cache = {}

# ============================================
# END CACHING LAYER
# ============================================

# Multiple user agents to rotate
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
]

# Content length threshold for Stage 2 AI enrichment
MIN_CONTENT_THRESHOLD = 300

# Multi-page crawling settings
MAX_ADDITIONAL_PAGES = 4
PRIORITY_PAGE_KEYWORDS = ['about', 'product', 'features', 'pricing', 'solution', 'team', 'how-it-works', 'how_it_works', 'howitworks', 'why', 'services', 'platform']
PAGE_CRAWL_TIMEOUT = 10  # seconds per page

# Website type classification constants
WEBSITE_TYPES = {
    'content_startup': 'Content-rich startup website with detailed about/product pages',
    'ecommerce': 'E-commerce or online store (Shopify, WooCommerce, etc.)',
    'simple_landing': 'Simple landing page with minimal content',
    'portfolio_brochure': 'Portfolio or brochure-style website',
    'unknown': 'Could not determine website type'
}


def detect_website_type(soup, url):
    """
    PHASE 2: Lightweight website type detection
    Classifies site into: content_startup, ecommerce, simple_landing, portfolio_brochure, unknown
    
    Uses:
    - DOM structure analysis
    - Cart/price/product grid detection
    - CMS markers (Shopify, WooCommerce, Wix, Webflow, Squarespace)
    - Page structure signals
    """
    signals = {
        'ecommerce': 0,
        'content_startup': 0,
        'simple_landing': 0,
        'portfolio_brochure': 0
    }
    
    html_str = str(soup).lower()
    
    # ===== E-COMMERCE DETECTION =====
    
    # 1. CMS Platform Detection (strong signals)
    ecommerce_platforms = {
        'shopify': [
            'cdn.shopify.com', 'shopify.com', 'myshopify.com',
            'shopify-section', 'shopify_analytics', 'ShopifyBuy'
        ],
        'woocommerce': [
            'woocommerce', 'wc-add-to-cart', 'add_to_cart',
            'wc-block', 'wp-content/plugins/woocommerce'
        ],
        'bigcommerce': [
            'bigcommerce.com', 'cdn.bigcommerce.com', 'bigcommerce-'
        ],
        'magento': [
            'magento', 'mage-', 'checkout/cart'
        ],
        'wix_stores': [
            'wixstores', 'wix-stores', '_api/wix-ecommerce'
        ],
        'squarespace_commerce': [
            'squarespace.com', 'static.squarespace', 'sqs-add-to-cart'
        ],
        'etsy': [
            'etsy.com', 'etsystatic.com'
        ],
        'amazon': [
            'amazon.com', 'amazon.', 'amzn.'
        ]
    }
    
    for platform, markers in ecommerce_platforms.items():
        for marker in markers:
            if marker in html_str:
                signals['ecommerce'] += 25
                break
    
    # 2. Cart/Checkout Indicators (strong signals)
    cart_indicators = [
        'add-to-cart', 'add_to_cart', 'addtocart', 'add to cart',
        'buy-now', 'buy_now', 'buynow', 'buy now',
        'shopping-cart', 'shopping_cart', 'shoppingcart',
        'cart-icon', 'cart_icon', 'basket', 'checkout',
        'data-product', 'data-variant', 'data-sku',
        'product-price', 'product_price', 'price-tag'
    ]
    
    for indicator in cart_indicators:
        if indicator in html_str:
            signals['ecommerce'] += 10
    
    # 3. Product Grid/Listing Detection
    product_selectors = [
        '.product-card', '.product-item', '.product-grid',
        '.product-list', '.products-grid', '.product-tile',
        '[data-product]', '[data-product-id]', '.product-image',
        '.shop-item', '.store-item', '.catalog-item'
    ]
    
    for selector in product_selectors:
        try:
            if soup.select(selector.replace('.', '[class*="').replace(']', '"]') if selector.startswith('.') else selector):
                signals['ecommerce'] += 8
        except:
            pass
    
    # Check for product-like elements by class name patterns
    product_class_patterns = ['product', 'shop', 'store', 'catalog', 'item-card', 'goods']
    for pattern in product_class_patterns:
        elements = soup.find_all(class_=lambda x: x and pattern in str(x).lower())
        if len(elements) > 3:  # Multiple product-like elements
            signals['ecommerce'] += 5
    
    # 4. Price Detection (strong signal for e-commerce)
    price_patterns = [
        r'\$\d+(?:\.\d{2})?', r'€\d+(?:\.\d{2})?', r'£\d+(?:\.\d{2})?',
        r'₹\d+', r'price', r'msrp', r'sale-price', r'regular-price'
    ]
    
    import re
    price_count = 0
    for pattern in price_patterns:
        matches = re.findall(pattern, html_str)
        price_count += len(matches)
    
    if price_count > 5:
        signals['ecommerce'] += 15
    elif price_count > 2:
        signals['ecommerce'] += 8
    
    # ===== CONTENT-RICH STARTUP DETECTION =====
    
    # 1. Navigation structure (multiple pages)
    nav_links = soup.find_all('nav')
    nav_items = []
    for nav in nav_links:
        nav_items.extend(nav.find_all('a'))
    
    startup_nav_keywords = [
        'about', 'team', 'features', 'pricing', 'solutions', 'product',
        'how it works', 'customers', 'case studies', 'resources', 'blog',
        'careers', 'contact', 'demo', 'partners', 'investors', 'press'
    ]
    
    nav_texts = [a.get_text(strip=True).lower() for a in nav_items]
    startup_nav_matches = sum(1 for keyword in startup_nav_keywords if any(keyword in text for text in nav_texts))
    
    if startup_nav_matches >= 4:
        signals['content_startup'] += 25
    elif startup_nav_matches >= 2:
        signals['content_startup'] += 15
    
    # 2. Startup-specific content indicators
    startup_indicators = [
        'founded in', 'our mission', 'our vision', 'our story',
        'how it works', 'why choose', 'our team', 'leadership',
        'backed by', 'investors', 'funding', 'series a', 'seed round',
        'customers include', 'trusted by', 'used by',
        'integrations', 'api', 'platform', 'saas', 'solution'
    ]
    
    for indicator in startup_indicators:
        if indicator in html_str:
            signals['content_startup'] += 5
    
    # 3. Feature sections detection
    feature_indicators = [
        'features', 'benefits', 'capabilities', 'what we offer',
        'why us', 'advantages', 'solutions'
    ]
    
    headings = soup.find_all(['h1', 'h2', 'h3'])
    heading_texts = [h.get_text(strip=True).lower() for h in headings]
    
    for indicator in feature_indicators:
        if any(indicator in text for text in heading_texts):
            signals['content_startup'] += 8
    
    # 4. Long-form content detection
    paragraphs = soup.find_all('p')
    long_paragraphs = [p for p in paragraphs if len(p.get_text(strip=True)) > 100]
    
    if len(long_paragraphs) > 5:
        signals['content_startup'] += 15
    elif len(long_paragraphs) > 2:
        signals['content_startup'] += 8
    
    # ===== SIMPLE LANDING PAGE DETECTION =====
    
    # 1. Single-page indicators
    single_page_markers = [
        'one-page', 'single-page', 'landing-page',
        '#section', 'scroll-to', 'smooth-scroll'
    ]
    
    for marker in single_page_markers:
        if marker in html_str:
            signals['simple_landing'] += 10
    
    # 2. Limited navigation (anchor links only)
    anchor_links = [a for a in nav_items if a.get('href', '').startswith('#')]
    if len(anchor_links) > 3 and len(nav_items) > 0:
        anchor_ratio = len(anchor_links) / len(nav_items)
        if anchor_ratio > 0.5:
            signals['simple_landing'] += 20
    
    # 3. Heavy CTA focus
    cta_keywords = ['sign up', 'get started', 'try free', 'subscribe', 'download', 'join', 'start now']
    cta_count = sum(1 for keyword in cta_keywords if keyword in html_str)
    
    if cta_count > 3 and len(long_paragraphs) < 3:
        signals['simple_landing'] += 15
    
    # 4. Limited content sections
    sections = soup.find_all(['section', 'div'], class_=lambda x: x and 'section' in str(x).lower())
    if 2 <= len(sections) <= 5 and len(long_paragraphs) < 5:
        signals['simple_landing'] += 10
    
    # ===== PORTFOLIO/BROCHURE DETECTION =====
    
    # 1. Portfolio indicators
    portfolio_keywords = [
        'portfolio', 'our work', 'projects', 'gallery', 'showcase',
        'case study', 'client work', 'selected work'
    ]
    
    for keyword in portfolio_keywords:
        if keyword in html_str:
            signals['portfolio_brochure'] += 15
    
    # 2. Image-heavy content
    images = soup.find_all('img')
    text_length = len(soup.get_text(strip=True))
    
    if len(images) > 10 and text_length < 3000:
        signals['portfolio_brochure'] += 12
    
    # 3. Brochure/agency markers
    brochure_keywords = [
        'services', 'what we do', 'our services', 'expertise',
        'capabilities', 'solutions we offer', 'industries we serve',
        'agency', 'studio', 'consulting', 'firm'
    ]
    
    for keyword in brochure_keywords:
        if keyword in html_str:
            signals['portfolio_brochure'] += 5
    
    # ===== DETERMINE WINNER =====
    
    # Get the highest scoring type
    max_score = max(signals.values())
    
    if max_score < 15:
        # Not enough signals - try to make a reasonable guess
        if price_count > 0 or any(marker in html_str for marker in ['cart', 'shop', 'buy']):
            return {
                'type': 'ecommerce',
                'confidence': 'low',
                'score': signals['ecommerce'],
                'all_scores': signals,
                'description': WEBSITE_TYPES['ecommerce']
            }
        elif len(long_paragraphs) > 3:
            return {
                'type': 'content_startup',
                'confidence': 'low',
                'score': signals['content_startup'],
                'all_scores': signals,
                'description': WEBSITE_TYPES['content_startup']
            }
        else:
            return {
                'type': 'simple_landing',
                'confidence': 'low',
                'score': signals['simple_landing'],
                'all_scores': signals,
                'description': WEBSITE_TYPES['simple_landing']
            }
    
    # Find the type(s) with max score
    winners = [t for t, s in signals.items() if s == max_score]
    winning_type = winners[0]
    
    # Determine confidence
    if max_score >= 40:
        confidence = 'high'
    elif max_score >= 25:
        confidence = 'medium'
    else:
        confidence = 'low'
    
    return {
        'type': winning_type,
        'confidence': confidence,
        'score': max_score,
        'all_scores': signals,
        'description': WEBSITE_TYPES.get(winning_type, WEBSITE_TYPES['unknown'])
    }


# ============================================
# PHASE 5: MULTI-PAGE CRAWLING (MAX 4 PAGES)
# ============================================

def discover_priority_links(soup, base_url):
    """
    Discover priority internal pages to crawl.
    Returns max 4 links for: about, product, features, pricing, solution, team, how-it-works
    """
    from urllib.parse import urljoin, urlparse
    
    priority_links = []
    seen_paths = set()
    base_domain = urlparse(base_url).netloc.lower()
    
    # Find all links in navigation areas first (higher priority)
    nav_areas = soup.find_all(['nav', 'header'])
    all_links = []
    
    for nav in nav_areas:
        all_links.extend(nav.find_all('a', href=True))
    
    # Also check footer for about/team links
    footer = soup.find('footer')
    if footer:
        all_links.extend(footer.find_all('a', href=True))
    
    # Fallback: all links on page
    if not all_links:
        all_links = soup.find_all('a', href=True)
    
    for link in all_links:
        if len(priority_links) >= MAX_ADDITIONAL_PAGES:
            break
            
        href = link.get('href', '').strip()
        link_text = link.get_text(strip=True).lower()
        
        # Skip empty, anchor, external, or special links
        if not href or href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:'):
            continue
        if href.startswith('javascript:'):
            continue
            
        # Build full URL
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        
        # Must be same domain
        if parsed.netloc.lower() != base_domain:
            continue
        
        # Check if path matches priority keywords
        path_lower = parsed.path.lower()
        
        # Skip if already seen this path
        if path_lower in seen_paths or path_lower == '/' or path_lower == '':
            continue
        
        # Check if path or link text contains priority keywords
        is_priority = False
        for keyword in PRIORITY_PAGE_KEYWORDS:
            if keyword in path_lower or keyword in link_text:
                is_priority = True
                break
        
        if is_priority:
            seen_paths.add(path_lower)
            priority_links.append(full_url)
            print(f"  📄 Found priority page: {full_url}")
    
    return priority_links[:MAX_ADDITIONAL_PAGES]


def scrape_additional_pages(base_url, links):
    """
    Scrape additional priority pages.
    Returns list of extracted data from each page.
    """
    additional_data = []
    
    for link in links:
        try:
            print(f"  🔄 Scraping additional page: {link}")
            
            headers = {
                'User-Agent': random.choice(USER_AGENTS),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }
            
            response = requests.get(link, headers=headers, timeout=PAGE_CRAWL_TIMEOUT, allow_redirects=True)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            page_data = extract_deterministic_content(soup, link)
            page_data['source_page'] = link
            additional_data.append(page_data)
            
        except Exception as e:
            print(f"  ⚠️ Failed to scrape {link}: {str(e)[:50]}")
            continue
    
    return additional_data


def merge_scraped_pages(main_data, additional_pages):
    """
    Merge additional page data into main scraped data.
    Strategy: Combine lists, don't overwrite company_name.
    """
    if not additional_pages:
        return main_data
    
    merged = main_data.copy()
    
    # Track pages scraped
    merged['pages_scraped'] = 1 + len(additional_pages)
    merged['additional_pages'] = [p.get('source_page', '') for p in additional_pages]
    
    for page_data in additional_pages:
        # Improve company name if main page has weak name (short domain fallback)
        main_name = merged.get('company_name', '')
        new_name = page_data.get('company_name', '')
        if main_name and len(main_name) <= 3 and new_name and len(new_name) > 3:
            merged['company_name'] = new_name
            print(f"  🎯 Upgraded company name from '{main_name}' to '{new_name}'")
        
        # Merge features (combine and dedupe)
        if page_data.get('features'):
            existing = set(merged.get('features', []))
            for feature in page_data['features']:
                if feature not in existing:
                    merged.setdefault('features', []).append(feature)
                    existing.add(feature)
        
        # Merge pricing (combine)
        if page_data.get('pricing'):
            existing_prices = merged.get('pricing', [])
            for price in page_data['pricing']:
                # For structured pricing, check plan name
                if isinstance(price, dict):
                    existing_plans = [p.get('plan', '') for p in existing_prices if isinstance(p, dict)]
                    if price.get('plan') not in existing_plans:
                        existing_prices.append(price)
                else:
                    if price not in existing_prices:
                        existing_prices.append(price)
            merged['pricing'] = existing_prices
        
        # Merge team (combine and dedupe)
        if page_data.get('team'):
            existing = set(merged.get('team', []))
            for member in page_data['team']:
                if member not in existing:
                    merged.setdefault('team', []).append(member)
                    existing.add(member)
        
        # Merge metrics (update with new keys, don't overwrite)
        if page_data.get('metrics'):
            for key, value in page_data['metrics'].items():
                if key not in merged.get('metrics', {}):
                    merged.setdefault('metrics', {})[key] = value
        
        # Append raw_text (with separator)
        if page_data.get('raw_text'):
            merged['raw_text'] = merged.get('raw_text', '') + '\n\n--- PAGE: ' + page_data.get('source_page', '') + ' ---\n\n' + page_data['raw_text'][:2000]
        
        # Merge headings
        if page_data.get('headings'):
            existing_texts = [h.get('text', '').lower() for h in merged.get('headings', [])]
            for heading in page_data['headings']:
                if heading.get('text', '').lower() not in existing_texts:
                    merged.setdefault('headings', []).append(heading)
    
    # Cap merged data
    if merged.get('features'):
        merged['features'] = merged['features'][:30]
    if merged.get('team'):
        merged['team'] = merged['team'][:15]
    if merged.get('raw_text'):
        merged['raw_text'] = merged['raw_text'][:8000]
    if merged.get('headings'):
        merged['headings'] = merged['headings'][:25]
    
    return merged

# ============================================
# END MULTI-PAGE CRAWLING
# ============================================


def extract_ecommerce_data(soup, url):
    """
    Enhanced extraction specifically for e-commerce sites.
    Extracts: products, categories, prices, collections.
    """
    ecommerce_data = {
        'products': [],
        'categories': [],
        'price_range': {'min': None, 'max': None},
        'collections': [],
        'store_features': []
    }
    
    import re
    html_str = str(soup).lower()
    
    # Extract products with more selectors
    product_selectors = [
        '.product-card', '.product-item', '.product', '[data-product]',
        '.product-tile', '.shop-item', '.grid-product', '.product-block',
        '.ProductItem', '.product-grid-item', '.product-list-item',
        'article[class*="product"]', 'div[class*="product-card"]',
        '.woocommerce-loop-product', '.shopify-section-product'
    ]
    
    products_found = set()
    for selector in product_selectors:
        try:
            elements = soup.select(selector)
            for el in elements[:20]:  # Limit to 20 products
                # Try to get product title
                title = None
                title_el = el.find(['h2', 'h3', 'h4', 'a', 'span'], class_=lambda x: x and 'title' in str(x).lower() or 'name' in str(x).lower())
                if title_el:
                    title = title_el.get_text(strip=True)
                elif el.find('a'):
                    title = el.find('a').get_text(strip=True)
                
                # Try to get price
                price = None
                price_el = el.find(class_=lambda x: x and 'price' in str(x).lower())
                if price_el:
                    price = price_el.get_text(strip=True)
                
                if title and len(title) > 2 and title not in products_found:
                    products_found.add(title)
                    ecommerce_data['products'].append({
                        'name': title[:100],
                        'price': price
                    })
        except:
            continue
    
    # Extract categories from navigation
    nav = soup.find('nav') or soup.find('header')
    if nav:
        category_keywords = ['shop', 'products', 'collections', 'categories', 'catalog', 'store']
        links = nav.find_all('a')
        for link in links:
            href = link.get('href', '').lower()
            text = link.get_text(strip=True)
            if any(kw in href or kw in text.lower() for kw in category_keywords):
                if text and len(text) < 50:
                    ecommerce_data['categories'].append(text)
    
    # Also look for category menus
    category_selectors = [
        '.category-menu', '.shop-categories', '.product-categories',
        '.collection-list', 'ul[class*="category"]', 'nav[class*="shop"]'
    ]
    
    for selector in category_selectors:
        try:
            menu = soup.select_one(selector)
            if menu:
                items = menu.find_all('a')
                for item in items[:10]:
                    text = item.get_text(strip=True)
                    if text and text not in ecommerce_data['categories']:
                        ecommerce_data['categories'].append(text)
        except:
            pass
    
    # Extract all prices for range calculation
    price_pattern = r'[\$€£₹][\s]*(\d+(?:[,\.]\d+)?)'
    prices = re.findall(price_pattern, str(soup))
    
    if prices:
        try:
            numeric_prices = [float(p.replace(',', '')) for p in prices]
            ecommerce_data['price_range']['min'] = min(numeric_prices)
            ecommerce_data['price_range']['max'] = max(numeric_prices)
        except:
            pass
    
    # Detect store features
    feature_indicators = {
        'free_shipping': ['free shipping', 'free delivery', 'ships free'],
        'returns': ['free returns', 'easy returns', 'return policy', '30 day return'],
        'secure_checkout': ['secure checkout', 'ssl', 'secure payment', 'encrypted'],
        'reviews': ['customer reviews', 'ratings', 'stars', 'testimonials'],
        'discount': ['sale', 'discount', '% off', 'clearance', 'deal']
    }
    
    for feature, indicators in feature_indicators.items():
        if any(ind in html_str for ind in indicators):
            ecommerce_data['store_features'].append(feature)
    
    # Remove duplicates
    ecommerce_data['categories'] = list(set(ecommerce_data['categories']))[:10]
    
    return ecommerce_data


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "service": "scraper-api", "cache_size": len(scrape_cache)})

@app.route('/cache/clear', methods=['POST'])
def clear_scrape_cache():
    """Clear scrape cache (all or specific URL)"""
    data = request.get_json() or {}
    url = data.get('url')
    
    clear_cache(url)
    
    return jsonify({
        "success": True,
        "message": f"Cache cleared for {'all URLs' if not url else url}",
        "cache_size": len(scrape_cache)
    })

@app.route('/cache/stats', methods=['GET'])
def cache_stats():
    """Get cache statistics"""
    return jsonify({
        "size": len(scrape_cache),
        "ttl_seconds": CACHE_TTL_SECONDS,
        "urls": list(scrape_cache.keys())[:20]  # Limit to 20 for readability
    })

@app.route('/scrape', methods=['POST'])
def scrape():
    try:
        start_time = time.time()
        data = request.get_json()
        url = data.get('url')
        skip_cache = data.get('skip_cache', False)  # Allow force refresh
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        # Ensure URL has scheme
        if not url.startswith('http'):
            url = 'https://' + url
        
        # PHASE 4: Check cache first (unless skip_cache=True)
        if not skip_cache:
            cached = get_cached_scrape(url)
            if cached:
                elapsed = time.time() - start_time
                print(f"⚡ Returning cached result in {elapsed:.2f}s")
                return jsonify({
                    "success": True,
                    "data": cached,
                    "cached": True,
                    "timing_ms": int(elapsed * 1000)
                })
        
        # Retry logic with different user agents
        response = None
        last_error = None
        
        for attempt in range(3):
            try:
                headers = {
                    'User-Agent': random.choice(USER_AGENTS),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Cache-Control': 'max-age=0',
                }
                
                response = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
                response.raise_for_status()
                break  # Success
            except requests.RequestException as e:
                last_error = e
                if attempt < 2:
                    time.sleep(1)  # Wait before retry
                continue
        
        if response is None:
            return jsonify({"error": f"Failed to fetch URL after 3 attempts: {str(last_error)}"}), 400
        
        # Parse HTML
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # PHASE 2: Detect website type FIRST
        website_type = detect_website_type(soup, url)
        print(f"🔍 Website Type Detection: {website_type['type']} (confidence: {website_type['confidence']}, score: {website_type['score']})")
        
        # STAGE 1: Deterministic extraction (NO AI)
        scraped_data = extract_deterministic_content(soup, url)
        
        # ============================================
        # PLAYWRIGHT FALLBACK FOR JS-HEAVY SITES
        # If raw_text is too short, the page likely uses JS rendering
        # ============================================
        raw_text_len = len(scraped_data.get('raw_text', ''))
        print(f"📝 Initial raw_text length: {raw_text_len}")
        
        if raw_text_len < MIN_RAW_TEXT_LENGTH and PLAYWRIGHT_AVAILABLE:
            print(f"🎭 Raw text too short ({raw_text_len} < {MIN_RAW_TEXT_LENGTH}) - attempting Playwright render...")
            
            rendered_html = render_with_playwright(url)
            
            if rendered_html and len(rendered_html) > len(response.content):
                print(f"🎭 Rendered HTML length: {len(rendered_html)}")
                
                # Re-parse with Playwright-rendered HTML
                soup = BeautifulSoup(rendered_html, 'html.parser')
                
                # Re-detect website type with full content
                website_type = detect_website_type(soup, url)
                print(f"🔍 Re-detected website type: {website_type['type']} (after JS render)")
                
                # Re-extract content with full DOM
                scraped_data = extract_deterministic_content(soup, url)
                scraped_data['js_rendered'] = True
                
                new_raw_text_len = len(scraped_data.get('raw_text', ''))
                print(f"📝 New raw_text length after JS render: {new_raw_text_len}")
            else:
                print(f"⚠️ Playwright render didn't improve content")
                scraped_data['js_rendered'] = False
        else:
            scraped_data['js_rendered'] = False
        
        # Add website type classification
        scraped_data['website_type'] = website_type
        
        # If e-commerce detected, extract enhanced e-commerce data
        if website_type['type'] == 'ecommerce':
            ecommerce_data = extract_ecommerce_data(soup, url)
            scraped_data['ecommerce'] = ecommerce_data
            print(f"🛒 E-commerce data: {len(ecommerce_data['products'])} products, {len(ecommerce_data['categories'])} categories")
        
        # PHASE 5: Multi-page crawling for richer data
        if website_type['type'] in ['content_startup', 'simple_landing', 'portfolio_brochure']:
            print(f"🔍 Discovering priority internal pages...")
            priority_links = discover_priority_links(soup, url)
            
            if priority_links:
                print(f"📚 Crawling {len(priority_links)} additional pages...")
                additional_pages = scrape_additional_pages(url, priority_links)
                scraped_data = merge_scraped_pages(scraped_data, additional_pages)
                print(f"✅ Merged data from {len(additional_pages)} additional pages")
        
        # Calculate content completeness score
        content_score = calculate_content_score(scraped_data)
        scraped_data['content_score'] = content_score
        scraped_data['needs_ai_enrichment'] = content_score < MIN_CONTENT_THRESHOLD
        
        # PHASE 4: Cache the result
        set_cached_scrape(url, scraped_data)
        
        elapsed = time.time() - start_time
        print(f"⏱️ Scrape completed in {elapsed:.2f}s")
        
        return jsonify({
            "success": True,
            "data": scraped_data,
            "cached": False,
            "timing_ms": int(elapsed * 1000)
        })
        
    except requests.RequestException as e:
        return jsonify({"error": f"Failed to fetch URL: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": f"Scraping failed: {str(e)}"}), 500


def extract_deterministic_content(soup, url):
    """
    STAGE 1: Pure deterministic scraping - NO AI, NO guessing
    Extract only what is explicitly present on the page
    """
    return {
        "url": url,
        "company_name": extract_company_name(soup, url),
        "title": extract_title(soup),
        "description": extract_description(soup),
        "hero_content": extract_hero_content(soup),
        "navigation_labels": extract_navigation(soup),
        "headings": extract_headings(soup),
        "ctas": extract_ctas(soup),
        "pricing": extract_pricing(soup),
        "features": extract_features(soup),
        "team": extract_team(soup),
        "metrics": extract_metrics(soup),
        "contact": extract_contact(soup),
        "social_links": extract_social_links(soup),
        "footer_info": extract_footer_info(soup),
        "image_alts": extract_image_alts(soup),
        "product_names": extract_product_names(soup),
        "raw_text": extract_clean_text(soup)
    }


def calculate_content_score(data):
    """
    Calculate content completeness score (0-100)
    Now website-type aware - e-commerce sites score differently than content sites
    """
    score = 0
    website_type = data.get('website_type', {}).get('type', 'unknown')
    
    # Base scoring (applies to all)
    if data.get('company_name'): score += 10
    if data.get('description') and len(data.get('description', '')) > 30: score += 10
    if data.get('hero_content'): score += 5
    if data.get('raw_text') and len(data.get('raw_text', '')) > 500: score += 10
    
    # Website-type specific scoring
    if website_type == 'ecommerce':
        # E-commerce: products and categories matter more
        ecommerce_data = data.get('ecommerce', {})
        products = ecommerce_data.get('products', [])
        categories = ecommerce_data.get('categories', [])
        
        if products and len(products) > 0: score += 20
        if len(products) > 5: score += 10
        if categories and len(categories) > 0: score += 15
        if data.get('pricing') and len(data.get('pricing', [])) > 0: score += 10
        if ecommerce_data.get('store_features'): score += 10
        
    elif website_type == 'content_startup':
        # Startup sites: features, team, metrics matter
        if data.get('features') and len(data.get('features', [])) > 0: score += 15
        if len(data.get('features', [])) > 3: score += 10
        if data.get('team') and len(data.get('team', [])) > 0: score += 15
        if data.get('metrics') and len(data.get('metrics', {})) > 0: score += 15
        if data.get('pricing') and len(data.get('pricing', [])) > 0: score += 10
        
    elif website_type == 'simple_landing':
        # Landing pages: CTAs and hero content matter
        if data.get('ctas') and len(data.get('ctas', [])) > 0: score += 15
        if data.get('hero_content') and data['hero_content'].get('headline'): score += 15
        if data.get('features') and len(data.get('features', [])) > 0: score += 10
        # Be more lenient with landing pages - they inherently have less content
        score += 20  # Bonus for landing pages
        
    elif website_type == 'portfolio_brochure':
        # Portfolio/Brochure: services and contact matter
        if data.get('features') and len(data.get('features', [])) > 0: score += 15
        if data.get('contact'): score += 15
        if data.get('image_alts') and len(data.get('image_alts', [])) > 3: score += 10
        score += 15  # Bonus - portfolio sites are naturally less text-heavy
        
    else:
        # Unknown/fallback: use original scoring
        if data.get('features') and len(data.get('features', [])) > 0: score += 15
        if data.get('pricing') and len(data.get('pricing', [])) > 0: score += 10
        if data.get('team') and len(data.get('team', [])) > 0: score += 10
        if data.get('metrics') and len(data.get('metrics', {})) > 0: score += 10
    
    return min(score, 100)


def extract_hero_content(soup):
    """Extract hero section content (headlines, taglines)"""
    hero = {}
    
    # Common hero section patterns
    hero_selectors = [
        'section.hero', 'div.hero', '.hero-section', '#hero',
        'header .headline', '.jumbotron', '.banner', '.masthead',
        '[class*="hero"]', '[id*="hero"]'
    ]
    
    for selector in hero_selectors:
        try:
            element = soup.select_one(selector)
            if element:
                hero['headline'] = element.find(['h1', 'h2'])
                if hero.get('headline'):
                    hero['headline'] = hero['headline'].get_text(strip=True)
                hero['tagline'] = element.find('p')
                if hero.get('tagline'):
                    hero['tagline'] = hero['tagline'].get_text(strip=True)[:200]
                break
        except:
            continue
    
    # Fallback: get first H1
    if not hero.get('headline'):
        h1 = soup.find('h1')
        if h1:
            hero['headline'] = h1.get_text(strip=True)
    
    return hero if hero else None


def extract_navigation(soup):
    """Extract navigation menu labels"""
    nav_labels = []
    
    nav = soup.find('nav') or soup.find('header')
    if nav:
        links = nav.find_all('a')
        for link in links[:15]:
            text = link.get_text(strip=True)
            if text and len(text) < 30 and text.lower() not in ['home', '#', '']:
                nav_labels.append(text)
    
    return list(set(nav_labels))[:10]


def extract_headings(soup):
    """Extract all meaningful headings (H1-H3)"""
    headings = []
    
    for tag in ['h1', 'h2', 'h3']:
        for heading in soup.find_all(tag)[:10]:
            text = heading.get_text(strip=True)
            if text and len(text) > 3 and len(text) < 150:
                headings.append({'level': tag, 'text': text})
    
    return headings[:15]


def extract_ctas(soup):
    """Extract call-to-action buttons and links"""
    ctas = []
    
    # Common CTA patterns
    cta_keywords = ['get started', 'sign up', 'try', 'buy', 'shop', 'order',
                    'contact', 'learn more', 'book', 'schedule', 'free trial',
                    'download', 'subscribe', 'join', 'request', 'demo']
    
    buttons = soup.find_all(['button', 'a'])
    for btn in buttons:
        text = btn.get_text(strip=True).lower()
        if any(kw in text for kw in cta_keywords) and len(text) < 50:
            ctas.append(btn.get_text(strip=True))
    
    return list(set(ctas))[:10]


def extract_footer_info(soup):
    """Extract footer company information"""
    footer_info = {}
    
    footer = soup.find('footer')
    if footer:
        # Copyright
        copyright_match = re.search(r'©\s*\d{4}[^<]*|copyright\s*\d{4}[^<]*', 
                                    footer.get_text(), re.I)
        if copyright_match:
            footer_info['copyright'] = copyright_match.group(0).strip()
        
        # Address
        address = footer.find('address')
        if address:
            footer_info['address'] = address.get_text(strip=True)[:200]
        
        # Links
        links = footer.find_all('a')
        footer_info['links'] = [l.get_text(strip=True) for l in links[:10] 
                                 if l.get_text(strip=True)]
    
    return footer_info if footer_info else None


def extract_image_alts(soup):
    """Extract image alt texts (useful for product info)"""
    alts = []
    
    for img in soup.find_all('img')[:20]:
        alt = img.get('alt', '').strip()
        if alt and len(alt) > 5 and len(alt) < 150:
            alts.append(alt)
    
    return list(set(alts))[:15]


def extract_product_names(soup):
    """Extract product/service names from common patterns"""
    products = []
    
    # Product card patterns
    product_selectors = [
        '.product-name', '.product-title', '[class*="product"] h2',
        '[class*="product"] h3', '.item-name', '.service-name'
    ]
    
    for selector in product_selectors:
        try:
            elements = soup.select(selector)
            for el in elements[:10]:
                text = el.get_text(strip=True)
                if text and len(text) < 100:
                    products.append(text)
        except:
            continue
    
    return list(set(products))[:10]


def extract_company_name(soup, url):
    """
    Extract company name from various sources.
    Priority: JSON-LD → og:site_name → title → domain
    """
    
    # HIGHEST PRIORITY: JSON-LD structured data
    try:
        json_ld_scripts = soup.find_all('script', type='application/ld+json')
        for script in json_ld_scripts:
            try:
                data = json.loads(script.string or '')
                # Handle array of objects
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get('@type') in ['Organization', 'WebSite', 'Corporation', 'LocalBusiness']:
                            name = item.get('name', '').strip()
                            if name and len(name) > 1 and len(name) < 100:
                                print(f"  ✅ Company name from JSON-LD: {name}")
                                return name
                # Single object
                elif isinstance(data, dict):
                    if data.get('@type') in ['Organization', 'WebSite', 'Corporation', 'LocalBusiness']:
                        name = data.get('name', '').strip()
                        if name and len(name) > 1 and len(name) < 100:
                            print(f"  ✅ Company name from JSON-LD: {name}")
                            return name
                    # Check @graph array
                    if '@graph' in data:
                        for item in data['@graph']:
                            if isinstance(item, dict) and item.get('@type') in ['Organization', 'WebSite', 'Corporation', 'LocalBusiness']:
                                name = item.get('name', '').strip()
                                if name and len(name) > 1 and len(name) < 100:
                                    print(f"  ✅ Company name from JSON-LD @graph: {name}")
                                    return name
            except (json.JSONDecodeError, AttributeError):
                continue
    except Exception:
        pass
    
    # Try to extract clear handle from URL path first (helps with LinkedIn, Twitter, Instagram)
    try:
        parts = url.split('/')
        if len(parts) > 3:
            domain = parts[2].lower()
            # LinkedIn company or profile pages
            if 'linkedin.com' in domain:
                for key in ['company', 'in', 'pub', 'school', 'groups']:
                    if key in parts:
                        idx = parts.index(key)
                        if idx + 1 < len(parts) and parts[idx+1]:
                            slug = parts[idx+1].strip()
                            slug = slug.split('?')[0].split('#')[0]
                            name = slug.replace('-', ' ').replace('_', ' ').title()
                            return name
            # Twitter, Instagram public handles
            if 'twitter.com' in domain or 'x.com' in domain:
                handle = parts[3] if len(parts) > 3 else ''
                if handle:
                    return handle.replace('_', ' ').title()
            if 'instagram.com' in domain or 'facebook.com' in domain:
                handle = parts[3] if len(parts) > 3 else ''
                if handle:
                    return handle.replace('_', ' ').title()
    except Exception:
        pass

    # Try meta tags
    og_site_name = soup.find('meta', property='og:site_name')
    if og_site_name:
        name = og_site_name.get('content', '').strip()
        if name and len(name) > 1:
            return name
    
    # Try title
    title = soup.find('title')
    if title and title.string:
        name = title.string.split('|')[0].split('-')[0].split('–')[0].split(':')[0].strip()
        if name and len(name) > 1 and len(name) < 80:
            return name
    
    # Extract from URL domain as last resort
    try:
        domain = url.split('/')[2].replace('www.', '').split('.')[0]
        return domain.capitalize()
    except Exception:
        return ''


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
    """
    Extract structured pricing information.
    Returns list of pricing plans with name, price, and features.
    """
    pricing = []
    
    # Keywords that indicate pricing sections
    pricing_section_keywords = ['pricing', 'plans', 'subscription', 'choose your plan', 
                                 'our plans', 'select a plan', 'membership', 'packages']
    
    # Find pricing sections by heading
    pricing_sections = []
    for heading in soup.find_all(['h1', 'h2', 'h3']):
        heading_text = heading.get_text(strip=True).lower()
        if any(kw in heading_text for kw in pricing_section_keywords):
            # Get the parent section
            parent = heading.find_parent(['section', 'div'])
            if parent:
                pricing_sections.append(parent)
    
    # Also look for pricing-specific classes
    pricing_selectors = [
        '[class*="pricing"]', '[class*="plans"]', '[class*="package"]',
        '[id*="pricing"]', '[id*="plans"]', '.price-table', '.pricing-table'
    ]
    
    for selector in pricing_selectors:
        try:
            elements = soup.select(selector)
            pricing_sections.extend(elements)
        except:
            pass
    
    # Extract structured pricing from each section
    seen_plans = set()
    
    for section in pricing_sections:
        # Look for individual plan cards
        plan_cards = section.find_all(['div', 'article'], class_=lambda x: x and (
            'plan' in str(x).lower() or 'price' in str(x).lower() or 
            'tier' in str(x).lower() or 'package' in str(x).lower() or
            'card' in str(x).lower()
        ))
        
        if not plan_cards:
            plan_cards = [section]
        
        for card in plan_cards:
            plan_data = {'plan': '', 'price': '', 'features': []}
            
            # Extract plan name (usually h3 or h4)
            plan_name_el = card.find(['h3', 'h4', 'h2'])
            if plan_name_el:
                plan_name = plan_name_el.get_text(strip=True)
                # Clean up plan name
                if len(plan_name) < 50 and not any(c in plan_name.lower() for c in ['$', '€', '£']):
                    plan_data['plan'] = plan_name
            
            # Extract price
            card_text = card.get_text()
            price_match = re.search(r'[\$€£]\s*(\d+(?:,\d{3})*(?:\.\d{2})?)(?:\s*/\s*(?:mo|month|yr|year|annually|per\s+month))?', card_text, re.I)
            if price_match:
                plan_data['price'] = price_match.group(0).strip()
            
            # Extract features (list items within the card)
            feature_list = card.find(['ul', 'ol'])
            if feature_list:
                for li in feature_list.find_all('li')[:8]:
                    feature_text = li.get_text(strip=True)
                    if feature_text and 10 < len(feature_text) < 150:
                        plan_data['features'].append(feature_text)
            
            # Only add if we have meaningful data
            if plan_data['plan'] or plan_data['price']:
                plan_key = f"{plan_data['plan']}_{plan_data['price']}"
                if plan_key not in seen_plans:
                    seen_plans.add(plan_key)
                    pricing.append(plan_data)
    
    # Fallback: extract raw prices if no structured data found
    if not pricing:
        raw_prices = re.findall(r'[\$€£]\s*\d+(?:,\d{3})*(?:\.\d{2})?(?:\s*/\s*(?:mo|month|yr|year))?', soup.get_text(), re.I)
        pricing = list(set(raw_prices))[:10]
    
    return pricing[:10]


def extract_features(soup):
    """
    Context-aware feature extraction.
    Only extract features from sections with relevant headings.
    """
    features = []
    seen_features = set()
    
    # Keywords that indicate feature sections
    feature_section_keywords = [
        'feature', 'capability', 'capabilities', 'solution', 'solutions',
        'why choose', 'what we offer', 'what we do', 'benefits', 'advantage',
        'how it works', 'our platform', 'our product', 'key features',
        'what you get', 'included', 'highlights', 'offerings'
    ]
    
    # Junk patterns to exclude (navigation, footer, legal)
    junk_patterns = [
        'privacy', 'terms', 'cookie', 'copyright', 'all rights',
        'sign up', 'log in', 'sign in', 'subscribe', 'newsletter',
        'follow us', 'connect with', 'social media'
    ]
    
    # Marketing fluff to exclude (generic promotional language)
    marketing_fluff = [
        'leading', 'best-in-class', 'industry leading', 'industry-leading',
        'award-winning', 'award winning', 'trusted by', 'world class', 'world-class',
        'revolutionary', 'innovative', 'cutting-edge', 'cutting edge',
        'next-generation', 'next generation', 'disruptive', 'game-changing',
        'game changer', 'paradigm shift', 'best in class', 'state-of-the-art',
        'state of the art', 'premier', 'unparalleled', 'unmatched'
    ]
    
    # Find all h2/h3 headings
    for heading in soup.find_all(['h2', 'h3']):
        heading_text = heading.get_text(strip=True).lower()
        
        # Check if heading indicates a feature section
        is_feature_section = any(kw in heading_text for kw in feature_section_keywords)
        
        if is_feature_section:
            # Get the next sibling elements or parent container
            parent = heading.find_parent(['section', 'div'])
            if parent:
                # Look for lists within this section
                lists = parent.find_all(['ul', 'ol'])
                for lst in lists:
                    for item in lst.find_all('li')[:10]:
                        text = item.get_text(strip=True)
                        text_lower = text.lower()
                        
                        # Validate feature text
                        if 20 < len(text) < 200:
                            # Skip junk
                            if any(junk in text_lower for junk in junk_patterns):
                                continue
                            # Skip marketing fluff
                            if any(fluff in text_lower for fluff in marketing_fluff):
                                continue
                            # Skip navigation-like items (too short, generic)
                            if len(text.split()) < 3:
                                continue
                            # Dedupe
                            if text_lower not in seen_features:
                                seen_features.add(text_lower)
                                features.append(text)
                
                # Also look for feature cards (divs with short text)
                feature_cards = parent.find_all(['div', 'article'], class_=lambda x: x and (
                    'feature' in str(x).lower() or 'card' in str(x).lower() or 'item' in str(x).lower()
                ))
                
                for card in feature_cards[:10]:
                    # Get the main text from the card
                    card_heading = card.find(['h3', 'h4', 'h5', 'strong'])
                    card_desc = card.find('p')
                    
                    if card_heading and card_desc:
                        combined = f"{card_heading.get_text(strip=True)}: {card_desc.get_text(strip=True)}"
                        if 20 < len(combined) < 200:
                            combined_lower = combined.lower()
                            if combined_lower not in seen_features:
                                if not any(junk in combined_lower for junk in junk_patterns):
                                    if not any(fluff in combined_lower for fluff in marketing_fluff):
                                        seen_features.add(combined_lower)
                                        features.append(combined)
                    elif card_desc:
                        text = card_desc.get_text(strip=True)
                        if 20 < len(text) < 200:
                            text_lower = text.lower()
                            if text_lower not in seen_features:
                                if not any(junk in text_lower for junk in junk_patterns):
                                    if not any(fluff in text_lower for fluff in marketing_fluff):
                                        seen_features.add(text_lower)
                                        features.append(text)
    
    # Fallback: if no context-aware features found, use original logic with stricter filtering
    if not features:
        # Look for named feature sections by class
        feature_selectors = ['[class*="feature"]', '[class*="benefit"]', '[class*="capability"]']
        for selector in feature_selectors:
            try:
                sections = soup.select(selector)
                for section in sections:
                    lists = section.find_all(['ul', 'ol'])
                    for lst in lists:
                        for item in lst.find_all('li')[:8]:
                            text = item.get_text(strip=True)
                            text_lower = text.lower()
                            if 20 < len(text) < 200 and len(text.split()) >= 3:
                                if not any(junk in text_lower for junk in junk_patterns):
                                    if not any(fluff in text_lower for fluff in marketing_fluff):
                                        if text_lower not in seen_features:
                                            seen_features.add(text_lower)
                                            features.append(text)
            except:
                pass
    
    return features[:25]  # Limit features


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


def normalize_metric_value(value):
    """
    Convert values like 500K, 2M, 1.5B into comparable integers.
    Ensures proper comparison: 2M > 500K
    """
    value = str(value).upper().strip()
    num = float(re.sub(r'[^\d.]', '', value) or 0)
    
    if 'B' in value:
        num *= 1_000_000_000
    elif 'M' in value:
        num *= 1_000_000
    elif 'K' in value:
        num *= 1_000
    
    return num


def extract_metrics(soup):
    """
    Extract key metrics and numbers.
    Scans headings, large bold numbers, and stat sections.
    """
    metrics = {}
    
    # Enhanced patterns for metric detection
    metric_patterns = [
        # Users/Customers
        (r'(\d+(?:,\d{3})*(?:\.\d+)?[KMB]?)\+?\s*(?:users|customers|clients|subscribers|members)', 'users'),
        (r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:million|M)\s*(?:users|customers|clients)', 'users'),
        
        # Revenue/Funding
        (r'\$\s*(\d+(?:,\d{3})*(?:\.\d+)?[KMB]?)\s*(?:funding|raised|revenue|ARR|MRR)', 'funding'),
        (r'\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:M|million)\s*(?:funding|raised|revenue)?', 'funding'),
        (r'series\s+[a-d]\s*(?:of)?\s*\$?\s*(\d+(?:\.\d+)?[KMB]?)', 'funding'),
        
        # Geographic reach
        (r'(\d+)\+?\s*(?:countries|cities|markets|locations|regions)', 'geographic_reach'),
        
        # Growth
        (r'(\d+(?:\.\d+)?[x%])\s*(?:growth|increase|improvement|faster)', 'growth'),
        (r'(\d+)%\s*(?:YoY|year.over.year|annual)\s*(?:growth)?', 'yoy_growth'),
        
        # Ratings
        (r'(\d+(?:\.\d)?)/5\s*(?:rating|stars?|review)', 'rating'),
        (r'(\d+(?:\.\d)?)\s*(?:stars?|rating)\s*(?:on)?\s*(?:G2|Capterra|Trustpilot)?', 'rating'),
        
        # Transactions/Volume
        (r'\$?(\d+(?:,\d{3})*(?:\.\d+)?[KMB]?)\+?\s*(?:transactions|orders|processed|volume)', 'transaction_volume'),
        
        # Time-based savings
        (r'(\d+)%?\s*(?:time|hours?)\s*(?:saved|reduction|faster)', 'time_saved'),
        
        # Team/Employees
        (r'(\d+)\+?\s*(?:employees|team members|staff|engineers)', 'team_size'),
        
        # Partners/Integrations
        (r'(\d+)\+?\s*(?:partners|integrations|apps|plugins)', 'integrations'),
        
        # Satisfaction
        (r'(\d+)%\s*(?:satisfaction|CSAT|NPS|happy\s+customers)', 'satisfaction'),
    ]
    
    text = soup.get_text()
    
    # Apply all patterns
    for pattern, key in metric_patterns:
        if key not in metrics:  # Don't overwrite
            matches = re.findall(pattern, text, re.I)
            if matches:
                # Take the most impressive (largest) value for that key using proper normalization
                metrics[key] = matches[0] if len(matches) == 1 else max(matches, key=lambda x: normalize_metric_value(x))
    
    # Scan for stat-like sections (common pattern: large number + description)
    stat_sections = soup.find_all(['div', 'span', 'p'], class_=lambda x: x and (
        'stat' in str(x).lower() or 'metric' in str(x).lower() or 
        'number' in str(x).lower() or 'counter' in str(x).lower()
    ))
    
    for section in stat_sections:
        section_text = section.get_text(strip=True)
        # Look for pattern: number + label
        stat_match = re.match(r'^([\$]?[\d,]+[KMB%+]*)\s*(.{3,30})$', section_text)
        if stat_match:
            value, label = stat_match.groups()
            label_clean = label.lower().strip()
            # Map common labels
            label_mapping = {
                'user': 'users', 'customer': 'users', 'client': 'users',
                'countr': 'countries', 'cit': 'cities',
                'partner': 'partners', 'integration': 'integrations',
                'transaction': 'transactions', 'order': 'orders'
            }
            for key_fragment, metric_key in label_mapping.items():
                if key_fragment in label_clean and metric_key not in metrics:
                    metrics[metric_key] = value
                    break
    
    # Look for bold/large numbers in hero sections
    hero_stats = soup.find_all(['strong', 'b', 'span'], class_=lambda x: x and 'hero' in str(x).lower())
    for stat in hero_stats:
        stat_text = stat.get_text(strip=True)
        if re.match(r'^[\$]?\d+[KMB%+]*$', stat_text):
            # Try to find adjacent label
            next_sibling = stat.find_next_sibling()
            if next_sibling:
                label = next_sibling.get_text(strip=True).lower()
                if 'download' in label and 'downloads' not in metrics:
                    metrics['downloads'] = stat_text
                elif 'review' in label and 'reviews' not in metrics:
                    metrics['reviews'] = stat_text
    
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
