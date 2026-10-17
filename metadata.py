"""Steam store metadata retrieval, independent of the library and UI."""
import html
import json
import re
import urllib.parse
import urllib.request
from playlite.sorting_name import sorting_name
from playlite.description_html import without_images


from playlite.metadata import MetadataError, release_date, merge_links, download_artwork


from playlite.metadata import link_name as link_name, friendly_links as friendly_links


def request(url, limit=4 * 1024 * 1024):
    req = urllib.request.Request(url, headers={'User-Agent': 'Playlite/0.1', 'Accept-Language': 'en'})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            content = response.read(limit + 1)
    except (OSError, ValueError) as error:
        raise MetadataError(f'Could not download from Steam: {error}') from error
    if len(content) > limit:
        raise MetadataError('Steam returned a response that is too large.')
    return content


def request_json(url):
    try:
        return json.loads(request(url))
    except (ValueError, UnicodeError) as error:
        raise MetadataError('Steam returned an invalid response. Please try again.') from error


def steam_id(text):
    text = text.strip()
    if text.isascii() and text.isdigit() and 0 < int(text) < 2**32:
        return int(text)
    parsed = urllib.parse.urlsplit(text)
    if parsed.hostname == 'store.steampowered.com':
        match = re.match(r'^/app/(\d+)(?:/|$)', parsed.path)
        if match and 0 < int(match[1]) < 2**32:
            return int(match[1])
    return None


def search_games(query):
    direct = steam_id(query)
    if direct:
        name = f'Steam app {direct}'
        try:
            resolved = fetch_metadata(direct).get('name')
            if isinstance(resolved, str) and resolved.strip():
                name = resolved
        except MetadataError:
            pass
        return [{'id': direct, 'name': name}]
    if not query.strip():
        raise MetadataError('Enter a game name, Steam app ID, or Steam store URL.')
    params = urllib.parse.urlencode({'term': query.strip(), 'l': 'english', 'cc': 'CA'})
    response = request_json('https://store.steampowered.com/api/storesearch/?' + params)
    if not isinstance(response, dict) or not isinstance(response.get('items'), list):
        raise MetadataError('Steam returned an unexpected search response.')
    return [{'id': int(item['id']), 'name': item['name']} for item in response['items']
            if isinstance(item, dict) and isinstance(item.get('id'), int) and isinstance(item.get('name'), str)]


def normalize(app_id, data):
    result = {}
    if data.get('name'):
        result['SortingName'] = sorting_name(data['name'])
    for key, source in [('Name', 'name'), ('Developers', 'developers'), ('Publishers', 'publishers')]:
        if data.get(source):
            result[key] = data[source]
    full_description = data.get('about_the_game')
    if full_description:
        result['FullDescription'] = without_images(full_description)
    description = data.get('short_description')
    if description:
        result['Description'] = html.unescape(description)
    for key, source in [('Genres', 'genres'), ('Features', 'categories')]:
        values = [html.unescape(item['description']) for item in data.get(source, []) if item.get('description')]
        if values:
            result[key] = list(dict.fromkeys(values))
    supported = data.get('platforms') or {}
    platforms = [name for key, name in [('windows', 'PC (Windows)'), ('linux', 'Linux'), ('mac', 'macOS')]
                 if supported.get(key)]
    if platforms:
        result['Platforms'] = platforms
    date = release_date((data.get('release_date') or {}).get('date', ''))
    if date:
        result['ReleaseDate'] = {'ReleaseDate': date}
    score = (data.get('metacritic') or {}).get('score')
    if isinstance(score, int) and 0 <= score <= 100:
        result['CriticScore'] = score
    result['Links'] = [{'Name': 'Steam', 'Url': f'https://store.steampowered.com/app/{app_id}/'}]
    website = data.get('website')
    if website and urllib.parse.urlsplit(website).scheme in ('http', 'https'):
        result['Links'].append({'Name': 'Official Website', 'Url': website})
    screenshots = data.get('screenshots') or []
    images = {}
    if screenshots and screenshots[0].get('path_full'):
        images['HeaderImage'] = screenshots[0]['path_full']
    elif data.get('header_image'):
        images['HeaderImage'] = data['header_image']
    if images.get('HeaderImage'):
        images['BackgroundImage'] = images['HeaderImage']
    # Portrait library artwork is optional; some games do not provide this asset.
    images['CoverImage'] = f'https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/library_600x900.jpg'
    return {'fields': result, 'images': images, 'name': data.get('name', f'Steam app {app_id}'), 'id': app_id}


def fetch_metadata(app_id):
    params = urllib.parse.urlencode({'appids': int(app_id), 'l': 'english', 'cc': 'CA'})
    response = request_json('https://store.steampowered.com/api/appdetails?' + params)
    entry = response.get(str(app_id), {}) if isinstance(response, dict) else {}
    if not entry.get('success') or not isinstance(entry.get('data'), dict):
        raise MetadataError('Steam could not find metadata for this app.')
    if entry['data'].get('type') not in ('game', 'dlc'):
        raise MetadataError('This result is not a game or DLC. Choose another result.')
    return normalize(app_id, entry['data'])

