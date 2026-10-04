from plugin_test_support import require_plugin
require_plugin('Steam')
import copy
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from playlite.editor import MetadataEditor
from playlite_plugins.steam.metadata import MetadataError, fetch_metadata, merge_links, normalize, search_games, steam_id
from playlite.metadata_dialog import MetadataDownloader
from playlite.providers import discover_providers

STORE_GAME = {'type': 'game', 'name': 'Test Game', 'developers': ['Studio'], 'publishers': ['Publisher'],
              'short_description': 'A game &amp; an adventure.', 'genres': [{'description': 'Adventure'}],
              'categories': [{'description': 'Single-player'}, {'description': 'Single-player'}],
              'platforms': {'windows': True, 'linux': True},
              'release_date': {'date': '28 May, 2024'}, 'metacritic': {'score': 88},
              'website': 'https://example.com', 'header_image': 'https://cdn.steamstatic.com/header.jpg'}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        fixture = discover_providers(include_disabled=True)
        patcher = patch('playlite.providers.discover_providers', return_value=fixture)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_wheel_over_cell_label_scrolls_table(self):
        from PyQt6.QtCore import QPoint, QPointF
        from PyQt6.QtGui import QWheelEvent
        from PyQt6.QtWidgets import QLabel, QAbstractItemView
        from playlite.metadata_dialog import MetadataTable
        table = MetadataTable(20, 1)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        label = QLabel('Cell content')
        table.setCellWidget(0, 0, label)
        table.resize(300, 180)
        table.show()
        QApplication.processEvents()
        event = QWheelEvent(QPointF(5, 5), QPointF(label.mapToGlobal(QPoint(5, 5))),
                            QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(label, event)
        self.assertEqual(table.verticalScrollBar().value(), 60)
        table.close()

    def test_remove_individual_list_defaults_preserves_other_defaults(self):
        with TemporaryDirectory() as directory:
            settings = Path(directory) / 'ui.ini'
            dialog = MetadataDownloader({'Name': 'Example'}, settings_path=settings, mode='metadata')
            action = dialog.list_identity('Action')
            rpg = dialog.list_identity('RPG')
            dialog.review_defaults = {'Name': {'source': 'Steam'}, 'Genres': {'checked': [action, rpg]}}
            dialog.remove_list_defaults([('Genres', action)])
            reopened = MetadataDownloader({'Name': 'Example'}, settings_path=settings, mode='metadata')
            self.assertEqual(reopened.review_defaults, {'Name': {'source': 'Steam'}, 'Genres': {'checked': [rpg]}})
            dialog.close()
            reopened.close()

    def test_save_ids_keeps_downloader_open(self):
        saved = []
        dialog = MetadataDownloader({'Name': 'Example'}, mode='metadata', save_ids=saved.append)
        dialog.id_fields['Steam'].setText('123')
        dialog.save_metadata_ids()
        self.assertEqual(saved, [{'Steam': '123'}])
        self.assertFalse(dialog.closed)
        self.assertEqual(dialog.current['MetadataIds'], {'Steam': '123'})
        dialog.close()

    def test_link_inferred_id_only_prefills_search(self):
        url = 'https://store.steampowered.com/app/123/'
        dialog = MetadataDownloader({'Name': 'Example', 'Links': [{'Name': 'Steam', 'Url': url}]}, mode='metadata')
        self.assertEqual(dialog.id_fields['Steam'].text(), '')
        self.assertNotIn('Steam', dialog.metadata_ids)
        dialog.provider_queue = ['Steam']
        with patch.object(dialog, 'search'):
            dialog.start_next_provider()
        self.assertEqual(dialog.query.text(), url)
        self.assertEqual(dialog.pages.currentIndex(), 1)
        with patch.object(dialog, 'choose_result') as choose:
            dialog.show_results([{'id': 123, 'name': 'Example'}], url)
            choose.assert_not_called()
        self.assertEqual(dialog.id_fields['Steam'].text(), '')
        dialog.reject()
        dialog.cache.cleanup()

    def test_explicit_id_hides_search_until_resolution_fails(self):
        dialog = MetadataDownloader({'Name': 'Example', 'MetadataIds': {'Steam': '123'}}, mode='metadata')
        dialog.id_fields['Steam'].setText('123')
        dialog.provider_queue = ['Steam']
        with patch.object(dialog, 'search'):
            dialog.start_next_provider()
        self.assertEqual(dialog.pages.currentIndex(), 0)
        with patch.object(dialog, 'choose_result') as choose:
            dialog.show_results([{'id': 123, 'name': 'Example'}], '123')
            choose.assert_called_once()
        self.assertEqual(dialog.pages.currentIndex(), 0)
        dialog.failed('Could not resolve ID')
        self.assertEqual(dialog.pages.currentIndex(), 1)
        self.assertTrue(dialog.search_button.isEnabled())
        dialog.close()

    def test_saved_ids_are_used_and_selection_records_id(self):
        dialog = MetadataDownloader({'Name': 'Example', 'MetadataIds': {'Steam': '123'}}, mode='metadata')
        self.assertEqual(dialog.id_fields['Steam'].text(), '123')
        dialog.change_provider('Steam')
        self.assertEqual(dialog.query.text(), '123')
        dialog.id_fields['IGDB'].setText('456')
        dialog.change_provider('IGDB')
        self.assertEqual(dialog.query.text(), '456')
        dialog.id_fields['IGDB'].clear()
        with patch.object(dialog, 'run_task'):
            dialog.show_results([{'id': 789, 'name': 'Example'}], 'https://igdb.com/games/example')
            dialog.choose_result(dialog.results.item(0))
        self.assertEqual(dialog.id_fields['IGDB'].text(), '789')
        dialog.prepared({})
        self.assertEqual(dialog.applied['MetadataIds'], {'Steam': '123', 'IGDB': '789'})

    def test_exact_source_results_skip_manual_selection(self):
        dialog = MetadataDownloader({'Name': 'Example'}, mode='metadata')
        cases = [
            ('Steam', 'https://store.steampowered.com/app/123/example/', [123], True),
            ('Steam', 'Example', [123], False),
            ('Steam', 'https://store.steampowered.com/app/123/', [456], False),
            ('IGDB', 'https://www.igdb.com/games/example', [123], True),
            ('IGDB', '123', [123], True),
            ('IGDB', 'Example', [123], False),
            ('IGDB', 'https://www.igdb.com/games/example', [123, 456], False),
        ]
        for provider, query, ids, automatic in cases:
            with self.subTest(provider=provider, query=query, ids=ids):
                dialog.provider = provider
                dialog.id_fields[provider].setText(query)
                with patch.object(dialog, 'choose_result') as choose:
                    dialog.show_results([{'id': game_id, 'name': 'Example'} for game_id in ids], query)
                    self.assertEqual(choose.call_count, int(automatic))
        dialog.close()

    def test_source_columns_default_on_and_bulk_controls_are_independent(self):
        dialog = MetadataDownloader({'Name': 'Example'}, mode='metadata')
        self.assertTrue(all(toggle.isChecked() == toggle.isEnabled() for toggles in dialog.source_toggles.values() for toggle in toggles.values()))
        dialog.column_toggles['Steam'].click()
        self.assertTrue(all(not toggles['Steam'].isChecked() and toggles['IGDB'].isChecked() == toggles['IGDB'].isEnabled() for toggles in dialog.source_toggles.values()))
        dialog.column_toggles['Steam'].click()
        self.assertTrue(all(toggles['Steam'].isChecked() == toggles['Steam'].isEnabled() for toggles in dialog.source_toggles.values()))
        self.assertTrue(dialog.skip_existing.isHidden())
        self.assertTrue(dialog.save_defaults.isHidden())
        dialog.reject()
        dialog.cache.cleanup()

    def test_review_defaults_lists_and_source_select_all(self):
        with TemporaryDirectory() as temporary:
            settings = Path(temporary) / 'ui.ini'
            current = {'Name': 'Current name', 'Genres': ['Old']}
            def create():
                dialog = MetadataDownloader(current, settings_path=settings, mode='metadata')
                dialog.selected_fields = ['Name', 'Genres']
                dialog.provider_payloads = {
                    'Steam': {'fields': {'Name': 'Steam name', 'Genres': ['Action']}},
                    'IGDB': {'fields': {'Name': 'IGDB name', 'Genres': ['RPG']}},
                }
                dialog.show_combined_preview()
                return dialog
            dialog = create()
            dialog.source_select_buttons['Steam'].click()
            dialog.choices['Genres']['options']['IGDB'][0][0].setChecked(True)
            dialog.set_review_defaults()
            dialog.reject()
            dialog.cache.cleanup()
            reopened = create()
            self.assertTrue(reopened.choices['Name']['options']['Steam'][0].isChecked())
            self.assertFalse(reopened.choices['Genres']['options']['Current'][0][0].isChecked())
            self.assertTrue(reopened.choices['Genres']['options']['Steam'][0][0].isChecked())
            self.assertTrue(reopened.choices['Genres']['options']['IGDB'][0][0].isChecked())
            reopened.apply()
            self.assertEqual(reopened.applied, {'Name': 'Steam name', 'Genres': ['Action', 'RPG']})
            reopened.cache.cleanup()
            cleared = create()
            cleared.unset_review_defaults()
            self.assertTrue(cleared.choices['Name']['options']['Current'][0].isChecked())
            self.assertTrue(cleared.choices['Genres']['options']['Current'][0][0].isChecked())
            self.assertFalse(cleared.settings.contains(cleared.defaults_key))
            cleared.reject()
            cleared.cache.cleanup()

    def test_multi_source_review_includes_empty_fields_and_radio_choices(self):
        dialog = MetadataDownloader({'Name': 'Current', 'Genres': ['Old']}, mode='metadata')
        for toggles in dialog.source_toggles.values():
            for toggle in toggles.values():
                toggle.setChecked(False)
        dialog.source_toggles['Name']['Steam'].setChecked(True)
        dialog.source_toggles['Genres']['Steam'].setChecked(True)
        dialog.source_toggles['Genres']['IGDB'].setChecked(True)
        dialog.selected_fields = ['Name', 'Genres', 'ReleaseDate']
        dialog.provider_payloads = {
            'Steam': {'fields': {'Name': 'Steam name', 'Genres': ['Steam genre']}},
            'IGDB': {'fields': {'Name': 'Ignored', 'Genres': ['IGDB genre']}},
        }
        dialog.show_combined_preview()
        self.assertEqual(dialog.preview.rowCount(), 4)
        self.assertIsNone(dialog.applied)
        self.assertNotIn('IGDB', dialog.choices['Name']['options'])
        dialog.choices['Name']['options']['Current'][0].setChecked(True)
        dialog.choices['Genres']['options']['Current'][0][0].setChecked(False)
        dialog.choices['Genres']['options']['IGDB'][0][0].setChecked(True)
        dialog.apply()
        self.assertEqual(dialog.applied, {'Genres': ['IGDB genre']})
        dialog.cache.cleanup()

    def test_direct_id_and_url(self):
        self.assertEqual(steam_id('1234'), 1234)
        self.assertEqual(steam_id('https://store.steampowered.com/app/1234/Test/'), 1234)
        self.assertIsNone(steam_id('https://evil.example/app/1234/'))
        self.assertIsNone(steam_id('0'))
        with patch('playlite_plugins.steam.metadata.request_json', return_value={'1234': {'success': True, 'data': STORE_GAME}}) as network:
            for query in ('1234', 'https://store.steampowered.com/app/1234/Test/'):
                self.assertEqual(search_games(query), [{'id': 1234, 'name': 'Test Game'}])
            self.assertEqual(network.call_count, 2)

    def test_direct_id_name_resolution_falls_back_when_unavailable(self):
        for response in ({}, {'1234': {'success': False}}, {'1234': {'success': True, 'data': {'type': 'game'}}}):
            with patch('playlite_plugins.steam.metadata.request_json', return_value=response):
                self.assertEqual(search_games('1234'), [{'id': 1234, 'name': 'Steam app 1234'}])
        with patch('playlite_plugins.steam.metadata.request_json', side_effect=MetadataError('Timeout')):
            self.assertEqual(search_games('1234'), [{'id': 1234, 'name': 'Steam app 1234'}])

    def test_normalization(self):
        fields = normalize(1234, STORE_GAME)['fields']
        self.assertEqual(fields['Description'], 'A game & an adventure.')
        self.assertEqual(fields['Features'], ['Single-player'])
        self.assertEqual(fields['ReleaseDate'], {'ReleaseDate': '2024-05-28'})
        self.assertEqual(fields['Platforms'], ['PC (Windows)', 'Linux'])
        self.assertEqual(fields['CriticScore'], 88)
        for key in ('InstallDirectory', 'LutrisId', 'IsInstalled', 'Playtime', 'Source'):
            self.assertNotIn(key, fields)

    def test_unavailable_fields_do_not_clear_existing_data(self):
        fields = normalize(1234, {'name': 'Upcoming', 'release_date': {'date': 'Coming soon'}})['fields']
        self.assertNotIn('ReleaseDate', fields)
        self.assertNotIn('Genres', fields)
        self.assertNotIn('Description', fields)

    def test_failed_or_non_game_lookup(self):
        for response in [{'1234': {'success': False}}, {'1234': {'success': True, 'data': {'type': 'music'}}}]:
            with patch('playlite_plugins.steam.metadata.request_json', return_value=response):
                with self.assertRaises(MetadataError):
                    fetch_metadata(1234)

    def test_link_merge_preserves_unrelated_links(self):
        existing = [{'Name': 'Discord', 'Url': 'https://example.com/discord'},
                    {'Name': 'Steam', 'Url': 'https://store.steampowered.com/app/1'}]
        original = copy.deepcopy(existing)
        result = merge_links(existing, [{'Name': 'Steam', 'Url': 'https://store.steampowered.com/app/1234/'}])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], existing[0])
        self.assertIn('/1234/', result[1]['Url'])
        self.assertEqual(existing, original)


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        fixture = discover_providers(include_disabled=True)
        patcher = patch('playlite.providers.discover_providers', return_value=fixture)
        patcher.start()
        self.addCleanup(patcher.stop)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(['playlite'])

    def test_metadata_includes_image_fields(self):
        dialog = MetadataDownloader({'Name': 'Original'}, mode='metadata')
        available = {item.data(Qt.ItemDataRole.UserRole) for item in dialog.field_items()}
        self.assertIn('Description', available)
        self.assertTrue({'Icon', 'CoverImage', 'HeaderImage', 'BackgroundImage'} <= available)
        for key in ('Icon', 'CoverImage', 'HeaderImage', 'BackgroundImage'):
            self.assertTrue(dialog.source_toggles[key]['Steam'].isEnabled())
        self.assertEqual(list(dialog.table_providers), ['Steam', 'IGDB'])
        dialog.reject()
        dialog.cache.cleanup()

    def test_preview_defaults_and_cancel(self):
        current = {'Name': 'Original', 'Links': []}
        dialog = MetadataDownloader(current)
        payload = normalize(1234, STORE_GAME)
        dialog.show_preview(payload)
        self.assertEqual(set(dialog.choices), {'Name'})
        self.assertTrue(dialog.choices['Name']['new'].isChecked())
        self.assertIn('Features', dialog.auto_fields)
        name = next(item for item in dialog.field_items() if item.data(Qt.ItemDataRole.UserRole) == 'Name')
        self.assertEqual(name.checkState(), Qt.CheckState.Unchecked)
        dialog.reject()
        dialog.prepared({'Name': 'Discarded'})
        self.assertIsNone(dialog.applied)
        self.assertEqual(current['Name'], 'Original')
        dialog.cache.cleanup()

    def test_apply_only_changes_selected_editor_fields(self):
        with TemporaryDirectory() as tmp:
            original = {'Id': 'test', 'Name': 'Original', 'Description': 'Keep me', 'Links': [],
                        'LutrisId': '18', 'InstallDirectory': '/games/test', 'Features': ['Old feature']}
            editor = MetadataEditor(original, Path(tmp))
            editor.apply_metadata({'Features': ['Single-player'], 'ReleaseDate': {'ReleaseDate': '2024-05-28'}})
            edited = editor.collect()
            self.assertEqual(edited['Description'], 'Keep me')
            self.assertEqual(edited['Name'], 'Original')
            self.assertEqual(edited['PlayActions'][0]['GameId'], '18')
            self.assertEqual(edited['Features'], ['Single-player'])
            self.assertEqual(original['Features'], ['Old feature'])
            self.assertFalse((Path(tmp) / 'library.json').exists())
            editor.reject()

    def test_unchecked_fields_and_artwork_are_not_applied(self):
        payload = normalize(1234, STORE_GAME)
        current = copy.deepcopy(payload['fields'])
        current.update(Name='Original', Features=['Old feature'])
        dialog = MetadataDownloader(current)
        dialog.show_preview(payload)
        dialog.select_values(False)
        for checkbox, _ in dialog.choices['Features']['current']:
            checkbox.setChecked(False)
        for checkbox, _ in dialog.choices['Features']['new']:
            checkbox.setChecked(True)
        with patch('playlite.metadata_dialog.download_artwork') as artwork:
            dialog.apply()
            artwork.assert_not_called()
        self.assertEqual(dialog.applied, {'Features': ['Single-player']})
        dialog.cache.cleanup()

    def test_field_selection_missing_only_and_saved_defaults(self):
        with TemporaryDirectory() as tmp:
            settings = Path(tmp) / 'ui.ini'
            dialog = MetadataDownloader({'Name': 'Original', 'Features': [], 'Links': []}, settings_path=settings)
            self.assertEqual(dialog.pages.currentIndex(), 0)
            for row in range(dialog.fields.rowCount()):
                item = dialog.fields.item(row, 0)
                item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in ('Name', 'Features')
                                   else Qt.CheckState.Unchecked)
            dialog.skip_existing.setChecked(True)
            dialog.save_defaults.setChecked(True)
            with patch.object(dialog, 'search'):
                dialog.next_step()
            self.assertEqual(dialog.pages.currentIndex(), 1)
            self.assertEqual(dialog.selected_fields, ['Features'])
            dialog.show_preview(normalize(1234, STORE_GAME))
            self.assertEqual(dialog.pages.currentIndex(), 2)
            self.assertEqual(dialog.preview.rowCount(), 0)
            self.assertEqual(dialog.applied, {'Features': ['Single-player']})
            dialog.cache.cleanup()
            reopened = MetadataDownloader({'Name': 'Original', 'Links': []}, settings_path=settings)
            selected = [reopened.fields.item(row, 0).data(Qt.ItemDataRole.UserRole)
                        for row in range(reopened.fields.rowCount())
                        if reopened.fields.item(row, 0).checkState() == Qt.CheckState.Checked]
            self.assertEqual(selected, ['Name', 'Features'])
            reopened.reject()
            reopened.cache.cleanup()

    def test_current_and_downloaded_list_entries_can_be_combined(self):
        payload = normalize(1234, STORE_GAME)
        current = copy.deepcopy(payload['fields'])
        current['Features'] = ['Controller support', 'Single-player']
        current['Links'] = [{'Name': 'Discord', 'Url': 'https://example.com/discord'}]
        dialog = MetadataDownloader(current)
        dialog.show_preview(payload)
        choice = dialog.choices['Features']
        choice['current'][0][0].setChecked(True)
        dialog.apply()
        self.assertEqual(dialog.applied['Features'], ['Controller support', 'Single-player'])
        self.assertEqual({link['Name'] for link in dialog.applied['Links']}, {'Discord', 'Steam', 'Official Website'})
        dialog.cache.cleanup()

    def test_unchanged_values_and_missing_values_skip_comparison(self):
        payload = normalize(1234, STORE_GAME)
        current = copy.deepcopy(payload['fields'])
        current['ReleaseDate'] = {'ReleaseDate': '2024-5-28'}
        current.pop('Publishers')
        dialog = MetadataDownloader(current)
        dialog.show_preview(payload)
        self.assertFalse(dialog.choices)
        self.assertEqual(dialog.applied, {'Publishers': ['Publisher']})
        dialog.cache.cleanup()


if __name__ == '__main__':
    unittest.main()
