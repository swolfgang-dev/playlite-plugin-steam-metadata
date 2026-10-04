from plugin_test_support import require_plugin
require_plugin('Steam')
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile
from PIL import Image
from PyQt6.QtGui import QImageReader
from playlite_plugins.steam.artwork import catalogue
from playlite.metadata import download_artwork


class CatalogueTests(unittest.TestCase):
    def test_localized_hashed_library_art_and_cdn_deduplication(self):
        common = {'library_assets_full': {
            'library_capsule': {'image': {'english': 'hash/library_600x900.jpg',
                                          'schinese': 'hash/chinese.jpg'},
                                'image2x': {'english': 'hash/library_600x900_2x.jpg'}},
            'library_hero_blur': {'image': {'english': 'hash/blur.jpg'}}}}
        store = {'capsule_image': 'https://cdn.akamai.steamstatic.com/steam/apps/620/hash/library_600x900.jpg?t=2',
                 'movies': [{'thumbnail': 'https://shared.fastly.steamstatic.com/poster.jpg'}]}
        result = catalogue(620, common, store)
        self.assertEqual(len(result['CoverImage']), 3)
        self.assertTrue(result['CoverImage'][0]['url'].endswith('_2x.jpg'))
        self.assertTrue(any(item['url'].endswith('chinese.jpg') for item in result['CoverImage']))
        self.assertTrue(any(item['url'].endswith('blur.jpg') for item in result['BackgroundImage']))
        self.assertTrue(any(item['label'] == 'Trailer poster 1' for item in result['HeaderImage']))

    def test_tga_and_linux_zip_decode_to_png(self):
        small, large = BytesIO(), BytesIO()
        Image.new('RGBA', (16, 16), 'red').save(small, format='PNG')
        Image.new('RGBA', (64, 64), 'blue').save(large, format='TGA')
        archive = BytesIO()
        with ZipFile(archive, 'w') as file:
            file.writestr('../small.png', small.getvalue())
            file.writestr('large.tga', large.getvalue())
        for extension, content in [('tga', large.getvalue()), ('zip', archive.getvalue())]:
            with self.subTest(extension=extension), TemporaryDirectory() as directory, patch(
                    'playlite.metadata.request', return_value=content):
                path = download_artwork('https://shared.fastly.steamstatic.com/icon.' + extension,
                                        Path(directory) / 'icon.img')
                self.assertEqual(Path(path).suffix, '.png')
                self.assertEqual(QImageReader(path).size().width(), 64)
                self.assertFalse((Path(directory).parent / 'small.png').exists())

    def test_complete_catalogue_is_shared_across_tabs(self):
        from playlite_plugins.steam.plugin import Provider
        provider = Provider()
        responses = [{'data': {'620': {'common': {'icon': 'a' * 40}}}},
                     {'620': {'success': True, 'data': {
                         'header_image': 'https://shared.fastly.steamstatic.com/header.jpg'}}}]
        with patch('playlite_plugins.steam.metadata.request_json', side_effect=responses) as request:
            provider.images(620, 'Icon')
            provider.images(620, 'CoverImage')
            provider.images(620, 'HeaderImage')
            provider.images(620, 'BackgroundImage')
            self.assertEqual(request.call_count, 2)
