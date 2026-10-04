from playlite.providers import MetadataProvider
from . import metadata
from urllib.parse import urlencode
import re
import html


class Provider(MetadataProvider):
    query_hint = 'Game name, Steam app ID, or Steam store URL'

    def search(self, query):
        return metadata.search_games(query)

    def fetch(self, game_id, fields):
        return metadata.fetch_metadata(game_id)

    def linked_query(self, game):
        return next((link.get('Url') for link in game.get('Links') or []
                     if metadata.steam_id(link.get('Url', ''))), None)

    def is_exact_query(self, query, result_id=None):
        game_id = metadata.steam_id(query)
        return game_id is not None and (result_id is None or str(game_id) == str(result_id))

    image_types = frozenset(('Icon', 'CoverImage', 'HeaderImage', 'BackgroundImage'))
    manual = True

    def query(self, game):
        saved = (game.get('MetadataIds') or {}).get('Steam')
        if saved and metadata.steam_id(str(saved)):
            return str(saved)
        return next((link['Url'] for link in game.get('Links') or []
                     if metadata.steam_id(link.get('Url', ''))), game.get('Name', ''))

    def images(self, game_id, image_type):
        if image_type not in self.image_types:
            return []
        app_id = metadata.steam_id(str(game_id))
        if not app_id:
            raise metadata.MetadataError('Enter a valid Steam app ID.')
        from .artwork import catalogue
        from time import monotonic
        cache = getattr(self, '_artwork_cache', {})
        cached = cache.get(app_id)
        if cached and monotonic() - cached[0] < 300:
            return cached[1][image_type]
        common, store, errors = {}, {}, []
        try:
            response = metadata.request_json(f'https://api.steamcmd.net/v1/info/{app_id}')
            data = response.get('data', {}) if isinstance(response, dict) else {}
            entry = data.get(str(app_id), {}) if isinstance(data, dict) else {}
            common = entry.get('common', {}) if isinstance(entry, dict) else {}
            common = common if isinstance(common, dict) else {}
        except metadata.MetadataError as error:
            errors.append(str(error))
        # Store data adds screenshots, backgrounds, capsules and trailer posters.
        try:
            params = urlencode({'appids': app_id, 'l': 'english', 'cc': 'CA'})
            response = metadata.request_json('https://store.steampowered.com/api/appdetails?' + params)
            entry = response.get(str(app_id), {}) if isinstance(response, dict) else {}
            if isinstance(entry, dict) and entry.get('success') and isinstance(entry.get('data'), dict):
                store = entry['data']
        except metadata.MetadataError as error:
            errors.append(str(error))
        images = catalogue(app_id, common, store)
        if not any(candidate['label'] == 'Community icon' for candidate in images['Icon']):
            try:
                page = metadata.request(f'https://steamcommunity.com/app/{app_id}').decode('utf-8')
                match = re.search(r'class=["\']apphub_AppIcon["\'][^>]*>\s*<img[^>]*src=["\']([^"\']+)', page)
                if match:
                    url = html.unescape(match[1])
                    if not any(candidate['url'] == url for candidate in images['Icon']):
                        images['Icon'].append({'url': url, 'label': 'Community icon'})
            except (metadata.MetadataError, UnicodeError) as error:
                errors.append(str(error))
        # Cache only complete responses so visiting another tab can retry a failed source.
        if common and store:
            cache[app_id] = (monotonic(), images)
            if len(cache) > 32:
                cache.pop(next(iter(cache)))
            self._artwork_cache = cache
        if not common and not store and (image_type != 'Icon' or not images['Icon']):
            raise metadata.MetadataError(errors[-1] if errors else 'Steam artwork is unavailable for this app.')
        return images[image_type]
