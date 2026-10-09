"""Steam store metadata retrieval, independent of the library and UI."""
from datetime import datetime
import html
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
from playlite.sorting_name import sorting_name
from playlite.description_html import without_images


from playlite.metadata import MetadataError


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


def release_date(value):
    for pattern in ('%d %b, %Y', '%b %d, %Y', '%d %B, %Y', '%B %d, %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    return None


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


def merge_links(existing, incoming):
    result = [dict(link) for link in existing]
    for link in incoming:
        # Keep unrelated existing links; update the canonical Steam/website link.
        by_name = next((old for old in result if old['Name'].casefold() == link['Name'].casefold()), None) \
            if link['Name'].casefold() in ('steam', 'official website') else None
        if by_name:
            by_name.update(link)
        elif not any(old['Url'].rstrip('/') == link['Url'].rstrip('/') for old in result):
            result.append(dict(link))
    return result


def download_artwork(url, target):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or not (
            parsed.hostname == 'images.igdb.com' or parsed.hostname in ('cdn.steamgriddb.com', 'shared.fastly.steamstatic.com') or parsed.hostname.endswith('.steamstatic.com') or parsed.hostname.endswith('.akamaihd.net')):
        raise MetadataError('The source returned an unsupported artwork address.')
    from PyQt6.QtGui import QImageReader
    content = request(url, limit=20 * 1024 * 1024)
    if content.startswith(b'PK\x03\x04') or parsed.path.lower().endswith('.tga'):
        from io import BytesIO
        from PIL import Image, UnidentifiedImageError
        from zipfile import ZipFile, BadZipFile
        try:
            if content.startswith(b'PK\x03\x04'):
                choices = []
                with ZipFile(BytesIO(content)) as archive:
                    for entry in archive.infolist():
                        if entry.file_size > 20 * 1024 * 1024 or entry.is_dir():
                            continue
                        try:
                            image = Image.open(BytesIO(archive.read(entry)))
                            if image.width * image.height <= 40_000_000:
                                image.load()
                                choices.append(image.copy())
                        except (UnidentifiedImageError, OSError, ValueError):
                            continue
                if not choices:
                    raise ValueError('No images in icon archive')
                image = max(choices, key=lambda item: item.width * item.height)
            else:
                image = Image.open(BytesIO(content))
            output = BytesIO()
            image.convert('RGBA').save(output, format='PNG')
            content = output.getvalue()
            target = Path(target).with_suffix('.png')
        except (BadZipFile, OSError, ValueError, Image.DecompressionBombError) as error:
            raise MetadataError('Could not read the downloaded client icon.') from error
    target = Path(target)
    target.write_bytes(content)
    reader = QImageReader(str(target))
    if not reader.canRead():
        target.unlink(missing_ok=True)
        raise MetadataError('The downloaded artwork is not a readable image.')
    if bytes(reader.format()).lower() == b'ico':
        # Qt defaults to the first ICO frame, often only 16×16. Keep the largest.
        sizes = []
        for index in range(reader.imageCount()):
            if reader.jumpToImage(index):
                size = reader.size()
                sizes.append((size.width() * size.height(), index))
        if sizes:
            reader.jumpToImage(max(sizes)[1])
        image = reader.read()
        converted = target.with_suffix('.png')
        if image.isNull() or not image.save(str(converted), 'PNG'):
            target.unlink(missing_ok=True)
            raise MetadataError('Could not read the downloaded client icon.')
        if converted != target:
            target.unlink(missing_ok=True)
        target = converted
    return str(target)
