from plugin_test_support import require_plugin
require_plugin('SteamMetadata')
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidgetItem, QDialog
from playlite.providers import discover_plugins, discover_providers
from playlite.image_dialog import ImageDownloader
from playlite.metadata import MetadataError
from playlite.metadata_dialog import MetadataDownloader

APP = QApplication.instance() or QApplication([])


class SteamImageTests(unittest.TestCase):
    def setUp(self):
        self.provider = discover_providers()['SteamMetadata']
        self.provider.image_defaults = {}
        fixture = {'SteamMetadata': self.provider}
        for target in ('playlite.image_dialog.discover_providers', 'playlite.providers.discover_providers'):
            patcher = patch(target, return_value=fixture)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_opening_with_saved_id_searches_once_but_name_requires_search(self):
        for game, expected in [({'Name': 'Example', 'MetadataIds': {'SteamMetadata': '620'}}, 1),
                               ({'Name': 'Example'}, 0)]:
            with self.subTest(game=game):
                dialog = ImageDownloader(game)
                with patch.object(dialog, 'search') as search:
                    dialog.show()
                    APP.processEvents()
                    self.assertEqual(search.call_count, expected)
                    dialog.hide()
                    dialog.show()
                    APP.processEvents()
                    self.assertEqual(search.call_count, expected)
                dialog.reject()

    def test_exact_shape_ratios(self):
        from playlite.image_filters import matches_shape
        for value, width, height, general in [('2:3', 600, 900, 'portrait'),
                                             ('16:9', 1920, 1080, 'landscape'),
                                             ('96:31', 1920, 620, 'wide')]:
            with self.subTest(value=value):
                self.assertTrue(matches_shape(width, height, [value]))
                self.assertTrue(matches_shape(width * 2, height * 2, [value]))
                self.assertFalse(matches_shape(width + 1, height, [value]))
                self.assertTrue(matches_shape(width, height, [general]))
                self.assertTrue(matches_shape(width, height, ['square', value]))
        self.assertFalse(matches_shape(0, 0, ['2:3']))

    def test_resolution_ranges_include_both_sides_of_boundaries(self):
        from playlite.image_filters import matches_resolution
        for boundary, lower in [(256, 0), (512, 256), (1024, 512), (1920, 1024)]:
            with self.subTest(boundary=boundary):
                self.assertTrue(matches_resolution(boundary, [lower]))
                self.assertTrue(matches_resolution(boundary, [boundary]))
                self.assertFalse(matches_resolution(boundary + 1, [lower]))
                self.assertFalse(matches_resolution(boundary - 1, [boundary]))
        self.assertFalse(matches_resolution(512, []))

    def test_images_share_metadata_plugin_and_saved_id(self):
        self.assertNotIn('SteamImages', discover_plugins())
        self.assertEqual(self.provider.query({'Name': 'Example', 'MetadataIds': {'SteamMetadata': '620'}}), '620')
        self.assertEqual(self.provider.query({'Links': [{'Url': 'https://store.steampowered.com/app/620/'}]}),
                         'https://store.steampowered.com/app/620/')

    def test_tabs_share_full_catalogue_and_single_result_skips_picker(self):
        dialog = ImageDownloader({'Name': 'Example'})
        calls = []
        def candidates(game_id, kind):
            calls.append((game_id, kind))
            return [{'url': kind, 'label': kind}]
        with patch.object(dialog, 'run', side_effect=lambda function, complete: complete(function())), \
             patch.object(dialog.providers['SteamMetadata'], 'search', return_value=[{'id': 620, 'name': 'Example'}]) as search, \
             patch('playlite.image_dialog.run_dialog') as picker, \
             patch.object(dialog.providers['SteamMetadata'], 'images', side_effect=candidates), \
             patch('playlite.image_dialog.download_artwork', side_effect=lambda url, target: str(target)):
            dialog.search()
            self.assertEqual(calls, [(620, key) for key in dialog.image_keys if key in self.provider.image_types])
            for index in range(1, 4):
                dialog.tabs.setCurrentIndex(index)
                self.assertEqual(dialog.images.count(), 4)
                self.assertEqual(dialog.selected_game['id'], 620)
                self.assertEqual(sum(not dialog.images.item(i).isHidden() for i in range(4)), 1)
            self.assertEqual(search.call_count, 1)
            self.assertEqual(len(calls), 4)
            picker.assert_not_called()
        dialog.reject()

    def test_filters_allow_any_artwork_in_any_tab_and_reset_on_switch(self):
        from tempfile import TemporaryDirectory
        from PyQt6.QtGui import QPixmap, QColor
        with TemporaryDirectory() as directory:
            paths = []
            for name, width, height in [('icon', 64, 64), ('cover', 600, 900), ('hero', 1920, 620)]:
                path = str(Path(directory) / (name + '.png'))
                pixmap = QPixmap(width, height)
                pixmap.fill(QColor('red'))
                pixmap.save(path)
                paths.append(path)
            dialog = ImageDownloader({'Name': 'Example'})
            result = [('Icon', paths[0], {'Icon'}), ('Cover', paths[1], {'CoverImage'}),
                      ('Hero', paths[2], {'HeaderImage', 'BackgroundImage'})]
            dialog.show_images({'Icon': (result, []), 'CoverImage': (result, [])})
            category, shape, resolution = dialog.filters['Icon']
            self.assertEqual([dialog.images.item(i).isHidden() for i in range(3)], [False, True, True])
            category.all.click()
            self.assertTrue(all(not dialog.images.item(i).isHidden() for i in range(3)))
            shape.set_values(['wide'])
            self.assertEqual([dialog.images.item(i).isHidden() for i in range(3)], [True, True, False])
            dialog.select_image(dialog.images.item(2))
            self.assertEqual(dialog.applied['Icon'], paths[2])
            shape.all.click()
            resolution.set_values([1024, 1920])
            self.assertEqual([dialog.images.item(i).isHidden() for i in range(3)], [True, True, False])
            dialog.tabs.setCurrentIndex(1)
            self.assertEqual(dialog.filters['CoverImage'][0].values(), ['CoverImage'])
            dialog.tabs.setCurrentIndex(0)
            self.assertEqual(category.values(), ['Icon'])
            self.assertEqual(shape.values(), ['square', 'portrait', 'landscape', 'wide', '2:3', '16:9', '96:31'])
            self.assertEqual(resolution.values(), [0, 256, 512, 1024, 1920])
            self.assertEqual(dialog.applied['Icon'], paths[2])
            dialog.reject()

    def test_metadata_download_chooses_first_available_recommendation(self):
        dialog = MetadataDownloader({'Name': 'Example'}, mode='metadata')
        dialog.selected_fields = ['CoverImage']
        item = QListWidgetItem('Example')
        item.setData(Qt.ItemDataRole.UserRole, 620)
        downloaded = []
        with patch.object(dialog, 'run_task', side_effect=lambda function, complete, message: downloaded.append(function())), \
             patch.object(dialog.providers['SteamMetadata'], 'fetch', return_value={'images': {}, 'fields': {}}), \
             patch.object(dialog.providers['SteamMetadata'], 'images', return_value=[{'url': 'missing'}, {'url': 'best'}, {'url': 'extra'}]), \
             patch('playlite.metadata_dialog.download_artwork', side_effect=[MetadataError('404'), '/tmp/best.img']) as download:
            dialog.choose_result(item)
            self.assertEqual(download.call_count, 2)
        self.assertEqual(downloaded[0]['files'], {'CoverImage': '/tmp/best.img'})
        self.assertEqual(downloaded[0]['warnings'], [])
        dialog.reject()
        dialog.cache.cleanup()

    def test_search_text_is_shared_only_between_matching_providers(self):
        other = Mock(name='Other provider')
        other.name = 'Other'
        other.image_types = {'Icon'}
        other.query.return_value = 'Other game'
        other.query_hint = 'Other query'
        with patch('playlite.image_dialog.discover_providers', return_value={'SteamMetadata': self.provider, 'Other': other}):
            dialog = ImageDownloader({'Name': 'Example', 'MetadataIds': {'SteamMetadata': '620'}})
        search_patch = patch.object(dialog, 'search')
        search_patch.start()
        self.addCleanup(search_patch.stop)
        dialog.query.setText('597220')
        self.assertTrue(all(controls[1].text() == '597220' for controls in dialog.controls.values()))
        dialog.source.setCurrentIndex(1)
        self.assertEqual(dialog.query.text(), 'Other game')
        dialog.query.setText('Other ID')
        dialog.tabs.setCurrentIndex(1)
        self.assertEqual(dialog.query.text(), '597220')
        dialog.query.setText('123')
        dialog.tabs.setCurrentIndex(0)
        self.assertEqual(dialog.query.text(), 'Other ID')
        dialog.source.setCurrentIndex(0)
        self.assertEqual(dialog.query.text(), '123')
        dialog.source.setCurrentIndex(1)
        self.assertEqual(dialog.query.text(), 'Other ID')
        dialog.reject()

    def test_tab_activation_searches_id_and_retains_matching_results(self):
        dialog = ImageDownloader({'Name': 'Example', 'MetadataIds': {'SteamMetadata': '620'}})
        dialog.initial_search_scheduled = True
        with patch.object(dialog, 'run', side_effect=lambda function, complete: complete(function())), \
             patch.object(dialog.providers['SteamMetadata'], 'search', return_value=[{'id': 620, 'name': 'Example'}]) as search, \
             patch.object(dialog.providers['SteamMetadata'], 'images', return_value=[]):
            dialog.tabs.setCurrentIndex(1)
            search.assert_called_once_with('620')
            dialog.tabs.setCurrentIndex(2)
            self.assertEqual(search.call_count, 1)
            dialog.tabs.setCurrentIndex(1)
            self.assertEqual(search.call_count, 1)
            dialog.query.setText('123')
            dialog.tabs.setCurrentIndex(2)
            self.assertEqual(search.call_count, 2)
            search.assert_called_with('123')
            dialog.query.setText('Example title')
            dialog.tabs.setCurrentIndex(3)
            self.assertEqual(search.call_count, 2)
            dialog.query.clear()
            dialog.tabs.setCurrentIndex(0)
            self.assertEqual(search.call_count, 2)
        dialog.reject()

    def test_multiple_search_results_require_selection(self):
        dialog = ImageDownloader({'Name': 'Example'})
        results = [{'id': 1, 'name': 'First'}, {'id': 2, 'name': 'Second'}]
        def choose(picker):
            picker.games.setCurrentRow(1)
            return QDialog.DialogCode.Accepted
        with patch('playlite.image_dialog.run_dialog', side_effect=choose), patch.object(dialog, 'load_images') as load:
            dialog.show_games(results)
            self.assertEqual(dialog.selected_game, results[1])
            load.assert_called_once()
        dialog.reject()

    def test_steam_icon_candidate(self):
        page = b'<div class="apphub_AppIcon"><img src="https://shared.akamai.steamstatic.com/community_assets/images/apps/620/icon.jpg"></div>'
        with patch('playlite_plugins.steammetadata.metadata.request_json', side_effect=MetadataError('Unavailable')), \
             patch('playlite_plugins.steammetadata.metadata.request', return_value=page):
            self.assertEqual(self.provider.images(620, 'Icon')[0]['label'], 'Community icon')

    def test_client_and_community_icons_from_app_info(self):
        common = {'clienticon': '566ae07473b877f0450bef7193ae08dedb00108a',
                  'icon': '4adaff16db14b2cf3bcfda2c523f0d4d68e15d6f'}
        with patch('playlite_plugins.steammetadata.metadata.request_json', return_value={'data': {'774361': {'common': common}}}), \
             patch('playlite_plugins.steammetadata.metadata.request') as community:
            candidates = self.provider.images(774361, 'Icon')
            self.assertEqual([item['label'] for item in candidates], ['Client icon', 'Community icon'])
            self.assertTrue(candidates[0]['url'].endswith(common['clienticon'] + '.ico'))
            self.assertTrue(candidates[1]['url'].endswith(common['icon'] + '.jpg'))
            community.assert_not_called()

    def test_bad_icon_hash_is_not_used_as_url(self):
        page = b'<div class="apphub_AppIcon"><img src="https://shared.fastly.steamstatic.com/icon.jpg"></div>'
        with patch('playlite_plugins.steammetadata.metadata.request_json', return_value={'data': {'620': {'common': {'clienticon': '../invalid'}}}}), \
             patch('playlite_plugins.steammetadata.metadata.request', return_value=page):
            self.assertEqual(len(self.provider.images(620, 'Icon')), 1)

    def test_download_uses_largest_ico_frame(self):
        from io import BytesIO
        from PIL import Image
        from tempfile import TemporaryDirectory
        from PyQt6.QtGui import QImageReader
        from playlite.metadata import download_artwork
        content = BytesIO()
        Image.new('RGBA', (256, 256), 'red').save(content, format='ICO', sizes=[(16, 16), (256, 256)])
        with TemporaryDirectory() as directory, patch('playlite.metadata.request', return_value=content.getvalue()):
            target = Path(directory) / 'Icon.img'
            path = download_artwork('https://shared.fastly.steamstatic.com/icon.ico', target)
            self.assertEqual(QImageReader(path).size().width(), 256)
            self.assertEqual(Path(path).suffix, '.png')
            self.assertFalse(target.exists())

    def test_candidates_include_actual_store_urls_and_deduplicate(self):
        response = {'620': {'success': True, 'data': {
            'header_image': 'https://shared.fastly.steamstatic.com/header.jpg',
            'background_raw': 'https://shared.fastly.steamstatic.com/background.jpg',
            'screenshots': [{'path_full': 'https://shared.fastly.steamstatic.com/screen.jpg'},
                            {'path_full': 'https://shared.fastly.steamstatic.com/screen.jpg'}]}}}
        with patch('playlite_plugins.steammetadata.metadata.request_json', return_value=response):
            covers = self.provider.images(620, 'CoverImage')
            headers = self.provider.images(620, 'HeaderImage')
            self.assertEqual(len(covers), 2)
            self.assertEqual(len(headers), 4)
            self.assertEqual(headers[2]['url'], response['620']['data']['header_image'])
        with patch('playlite_plugins.steammetadata.metadata.request_json', return_value={'620': {'success': False}}):
            with self.assertRaises(MetadataError):
                self.provider.images(620, 'CoverImage')

    def test_selection_requires_apply_and_cancel_removes_temporary_files(self):
        dialog = ImageDownloader({'Name': 'Example', 'MetadataIds': {'SteamMetadata': '620'}})
        self.assertEqual(dialog.source.currentData(), 'SteamMetadata')
        self.assertEqual(dialog.query.text(), '620')
        path = Path(dialog.cache.name) / 'cover.img'
        path.write_bytes(b'example')
        item = QListWidgetItem('Cover')
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        with patch.object(dialog, 'search'):
            dialog.tabs.setCurrentIndex(1)
        dialog.images.addItem(item)
        dialog.images.setCurrentItem(item)
        self.assertEqual(dialog.applied, {})
        dialog.images.itemDoubleClicked.emit(item)
        self.assertEqual(dialog.applied, {'CoverImage': str(path)})
        self.assertTrue(item.data(Qt.ItemDataRole.UserRole + 1))
        alternate = QListWidgetItem('Another cover')
        alternate.setData(Qt.ItemDataRole.UserRole, str(path) + '.other')
        dialog.images.addItem(alternate)
        dialog.images.itemDoubleClicked.emit(alternate)
        self.assertFalse(item.data(Qt.ItemDataRole.UserRole + 1))
        self.assertTrue(alternate.data(Qt.ItemDataRole.UserRole + 1))
        self.assertEqual(dialog.applied['CoverImage'], str(path) + '.other')
        self.assertFalse(hasattr(dialog, 'use'))
        dialog.reject()
        self.assertFalse(path.exists())

    def test_unavailable_asset_does_not_block_other_candidates(self):
        dialog = ImageDownloader({'Name': 'Example'})
        dialog.tabs.setCurrentIndex(1)
        dialog.selected_game = {'id': 620, 'name': 'Example'}
        def run(function, complete):
            complete(function())
        with patch.object(dialog, 'run', side_effect=run), \
             patch.object(dialog.providers['SteamMetadata'], 'images', side_effect=lambda game_id, kind: [
                 {'url': 'missing', 'label': 'Missing'}, {'url': 'valid', 'label': 'Valid'}] if kind == 'CoverImage' else []), \
             patch('playlite.image_dialog.download_artwork', side_effect=[MetadataError('404'), '/tmp/valid.img']):
            dialog.load_images()
        self.assertEqual(dialog.image_lists['CoverImage'].count(), 1)
        self.assertEqual(dialog.tabs.count(), 5)
        self.assertIn('Missing: 404', dialog.status.toolTip())
        dialog.reject()

    def test_apply_retains_files_for_editor_save(self):
        dialog = ImageDownloader({'Name': 'Example'})
        path = Path(dialog.cache.name) / 'cover.img'
        path.write_bytes(b'example')
        dialog.applied['CoverImage'] = str(path)
        dialog.accept()
        self.assertTrue(path.exists())
        dialog.cache.cleanup()
