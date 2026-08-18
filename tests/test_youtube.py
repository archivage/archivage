import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archivage.youtube import collectionVideos, pickSubtitleLang, saveCollection


class SubtitleSelectionTests(unittest.TestCase):
    def test_live_chat_is_not_treated_as_manual_captions(self):
        info = {
            'subtitles': {'live_chat': [{'ext': 'json'}]},
            'automatic_captions': {'en-orig': [{'ext': 'srt'}]},
        }

        self.assertEqual(pickSubtitleLang(info), ('en-orig', True))


class CollectionVideosTests(unittest.TestCase):
    @patch('archivage.youtube.runYtDlp')
    def test_normalizes_and_deduplicates_entries(self, run):
        run.return_value.stdout = json.dumps({
            'entries': [
                {'id': 'abcdefghijk'},
                {'id': 'abcdefghijk'},
                {'url': 'https://youtu.be/lmnopqrstuv'},
                {'id': 'not-a-video-id'},
                None,
            ],
        })

        with tempfile.TemporaryDirectory() as tmp:
            videos = collectionVideos('https://youtube.test/@channel/videos', Path(tmp))

        self.assertEqual(videos, [
            'https://www.youtube.com/watch?v=abcdefghijk',
            'https://www.youtube.com/watch?v=lmnopqrstuv',
        ])

    @patch('archivage.youtube.runYtDlp')
    def test_passes_collection_limit_to_ytdlp(self, run):
        run.return_value.stdout = '{"entries": []}'

        with tempfile.TemporaryDirectory() as tmp:
            collectionVideos('https://youtube.test/playlist', Path(tmp), limit=7)

        self.assertIn('--playlist-end', run.call_args.args[0])
        self.assertIn('7', run.call_args.args[0])


class SaveCollectionTests(unittest.TestCase):
    @patch('archivage.youtube.shutil.which', return_value='/usr/bin/yt-dlp')
    @patch('archivage.youtube.saveVideo')
    @patch('archivage.youtube.collectionVideos')
    def test_continues_after_unavailable_transcript(self, videos, save, _which):
        videos.return_value = [
            'https://www.youtube.com/watch?v=abcdefghijk',
            'https://www.youtube.com/watch?v=lmnopqrstuv',
            'https://www.youtube.com/watch?v=uvwxyzABCDE',
        ]
        save.side_effect = [
            (Path('/archive/one.md'), 'saved'),
            RuntimeError('No subtitles available for this video'),
            (Path('/archive/three.md'), 'skipped'),
        ]

        summary = saveCollection('https://youtube.test/@channel/videos', Path('/archive'))

        self.assertEqual(summary['discovered'], 3)
        self.assertEqual(summary['saved'], 1)
        self.assertEqual(summary['skipped'], 1)
        self.assertEqual(len(summary['failed']), 1)
        self.assertIn('No subtitles', summary['failed'][0]['error'])


if __name__ == '__main__':
    unittest.main()
