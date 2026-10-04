"""Combine advertised Steam app-info and store artwork into one catalogue."""
import re
from urllib.parse import urlsplit

TYPES = ('Icon', 'CoverImage', 'HeaderImage', 'BackgroundImage')


def catalogue(app_id, common, store):
    base = f'https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{app_id}/'
    community = f'https://shared.fastly.steamstatic.com/community_assets/images/apps/{app_id}/'
    results = {key: [] for key in TYPES}
    seen = {key: set() for key in TYPES}

    def add(url, label, types, thumbnail=None):
        if not isinstance(url, str):
            return
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or not (
                parsed.hostname.endswith('.steamstatic.com') or parsed.hostname.endswith('.akamaihd.net')):
            return
        # Steam's CDN aliases and cache-busting queries identify the same asset.
        identity = re.sub(r'^/(?:store_item_assets/)?steam/apps/', '/apps/', parsed.path)
        identity = re.sub(r'^/(?:community_assets/images|steamcommunity/public/images)/apps/', '/community/', identity)
        for key in types:
            if identity not in seen[key]:
                seen[key].add(identity)
                results[key].append({'url': url, 'label': label, 'thumbnail': thumbnail or url})

    def asset_url(value):
        if not isinstance(value, str):
            return None
        if value.startswith('https://'):
            return value
        if (re.fullmatch(r'[a-zA-Z0-9_./-]+\.(?:jpg|jpeg|png|webp|ico|tga)', value, re.I)
                and '..' not in value.split('/') and not value.startswith('/')):
            return base + value
        return None

    def variants(value, label):
        if isinstance(value, dict):
            # Recommend English high-resolution assets before the other variants.
            priority = {'image2x': 0, 'image': 1, 'english': 0}
            for key in sorted(value, key=lambda key: (priority.get(key, 2), key)):
                yield from variants(value[key], label + ' · ' + str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from variants(item, label + f' · {index + 1}')
        else:
            url = asset_url(value)
            if url:
                yield url, label

    def family_types(name):
        name = name.casefold()
        if 'logo' in name:
            return ('Icon', 'HeaderImage')
        if 'icon' in name:
            return ('Icon',)
        if 'blur' in name or 'background' in name:
            return ('BackgroundImage',)
        if 'hero' in name:
            return ('HeaderImage', 'BackgroundImage')
        if 'library_capsule' in name or 'vertical' in name:
            return ('CoverImage',)
        if 'capsule' in name:
            return ('CoverImage', 'HeaderImage')
        if 'header' in name:
            return ('HeaderImage',)
        return ('CoverImage', 'HeaderImage', 'BackgroundImage')

    for key, extension, label in [('clienticon', 'ico', 'Client icon'), ('icon', 'jpg', 'Community icon'),
                                   ('clienttga', 'tga', 'Client icon (TGA)'),
                                   ('linuxclienticon', 'zip', 'Linux client icon')]:
        digest = common.get(key)
        if isinstance(digest, str) and re.fullmatch(r'[a-fA-F0-9]{40}', digest):
            add(community + digest + '.' + extension, label, ('Icon',))

    full = common.get('library_assets_full')
    if isinstance(full, dict):
        # Covers/heroes precede logos for automatic recommendations.
        for family in sorted(full, key=lambda key: ('logo' in key, key)):
            for url, label in variants(full[family], family.replace('_', ' ').title()):
                add(url, label, family_types(family))

    for key in ('header_image', 'small_capsule'):
        for url, label in variants(common.get(key), key.replace('_', ' ').title()):
            add(url, label, family_types(key))
    for key in ('logo', 'logo_small'):
        digest = common.get(key)
        if isinstance(digest, str) and re.fullmatch(r'[a-fA-F0-9]{40}(?:_thumb)?', digest):
            add(community + digest + '.jpg', key.replace('_', ' ').title(), ('Icon', 'HeaderImage'))
    # Include new advertised image families without depending on a fixed filename list.
    for family, value in common.items():
        if family not in ('library_assets_full', 'header_image', 'small_capsule') and any(
                token in family for token in ('image', 'capsule', 'logo', 'assets', 'background')):
            for url, label in variants(value, family.replace('_', ' ').title()):
                add(url, label, family_types(family))

    for key, value in store.items():
        if any(token in key for token in ('image', 'capsule', 'background')):
            for url, label in variants(value, 'Store ' + key.replace('_', ' ')):
                add(url, label, family_types(key))
    for index, screenshot in enumerate(store.get('screenshots') or []):
        if isinstance(screenshot, dict):
            add(screenshot.get('path_full'), f'Screenshot {index + 1}',
                ('HeaderImage', 'BackgroundImage'), screenshot.get('path_thumbnail'))
    # Trailer posters are images too; video files are deliberately not candidates.
    for index, movie in enumerate(store.get('movies') or []):
        if isinstance(movie, dict):
            add(movie.get('thumbnail'), f'Trailer poster {index + 1}', ('HeaderImage', 'BackgroundImage'))

    # Legacy fallbacks for apps whose current app-info omits library assets.
    for key, filenames in {
            'CoverImage': ('library_600x900_2x.jpg', 'library_600x900.jpg'),
            'HeaderImage': ('library_hero_2x.jpg', 'library_hero.jpg'),
            'BackgroundImage': ('library_hero_2x.jpg', 'library_hero.jpg')}.items():
        # Keep fallbacks ahead of store screenshots, but after advertised library art.
        if not any('Library ' in candidate['label'] for candidate in results[key]):
            previous = results[key]
            results[key] = []
            for filename in filenames:
                add(base + filename, 'Library artwork (fallback)', (key,))
            results[key].extend(previous)
    return results
