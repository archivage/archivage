import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archivage import state
from archivage.storage import normalizeTweetId, tweetIdentity
from archivage.twitter import AccountUnavailable, TwitterClient
from archivage.twitter_index import indexFile, readThread, readTweet, searchTweets


def sampleTweet(tweet_id: str, text: str, conversation_id: str | None = None):
    return {
        'core': {
            'user_results': {
                'result': {
                    'rest_id': '388426142',
                    'core': {
                        'screen_name': 'Markmanson',
                        'name': 'Mark Manson',
                    },
                },
            },
        },
        'legacy': {
            'id_str': tweet_id,
            'full_text': text,
            'conversation_id_str': conversation_id or tweet_id,
            'created_at': 'Fri Aug 15 12:00:00 +0000 2026',
        },
        'rest_id': tweet_id,
    }


class IdentityTests(unittest.TestCase):
    def test_tweet_identity_uses_stable_user_id(self):
        identity = tweetIdentity([sampleTweet('100', 'Archived tweet')])

        self.assertEqual(identity['user_id'], '388426142')
        self.assertEqual(identity['current_handle'], 'Markmanson')

    def test_state_retains_original_and_changed_handles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / 'state.json'
            with patch.object(state, '_stateFile', return_value=state_file):
                state.setAccountState(
                    'IAmMarkManson',
                    user_id='388426142',
                    current_handle='Markmanson',
                )
                account = state.getAccountState('IAmMarkManson')

        self.assertEqual(account['user_id'], '388426142')
        self.assertEqual(account['current_handle'], 'Markmanson')
        self.assertEqual(
            account['aliases'],
            ['IAmMarkManson', 'Markmanson'],
        )

    def test_normalizes_status_url(self):
        self.assertEqual(
            normalizeTweetId('https://x.com/Markmanson/status/1234567890?s=20'),
            '1234567890',
        )

    def test_transient_error_preserves_sync_boundaries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / 'state.json'
            with patch.object(state, '_stateFile', return_value=state_file):
                state.setAccountState(
                    'example',
                    newest_id='200',
                    oldest_id='100',
                    status='complete',
                )
                state.markAccountError('example', 'temporary failure')
                account = state.getAccountState('example')

        self.assertEqual(account['newest_id'], '200')
        self.assertEqual(account['oldest_id'], '100')
        self.assertEqual(account['status'], 'error')
        self.assertEqual(account['consecutive_errors'], 1)


class UnavailableAccountTests(unittest.TestCase):
    def test_deleted_account_is_classified_without_erasing_state(self):
        client = TwitterClient(Path('/tmp/not-used'))
        client._call = lambda *args, **kwargs: {
            'data': {
                'user': {
                    'result': {
                        '__typename': 'UserUnavailable',
                        'reason': 'AccountSuspended',
                    },
                },
            },
        }

        with self.assertRaises(AccountUnavailable):
            client.getUserId('AudrandS')

    def test_missing_user_result_is_unavailable(self):
        client = TwitterClient(Path('/tmp/not-used'))
        client._call = lambda *args, **kwargs: {
            'data': {'user': {}},
            'errors': [{'message': 'User is unavailable'}],
        }

        with self.assertRaisesRegex(AccountUnavailable, 'User is unavailable'):
            client.getUserTweets('731746399746961408')


class IndexTests(unittest.TestCase):
    def test_index_read_search_and_thread_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_file = root / 'IAmMarkManson.jsonl.gz'
            database = root / 'search.sqlite'
            tweets = [
                sampleTweet('100', 'Archived ideas remain searchable.'),
                sampleTweet('101', 'A reply in the same thread.', '100'),
            ]
            with gzip.open(archive_file, 'wt') as output:
                for tweet in tweets:
                    output.write(json.dumps(tweet) + '\n')

            self.assertEqual(indexFile(archive_file, 'IAmMarkManson', database), 2)
            self.assertEqual(indexFile(archive_file, 'IAmMarkManson', database), 0)
            self.assertEqual(readTweet('100', database)['handle'], 'Markmanson')
            self.assertEqual(searchTweets('searchable', path=database)[0]['id'], '100')
            self.assertEqual(
                [tweet['id'] for tweet in readThread('101', database)],
                ['100', '101'],
            )


if __name__ == '__main__':
    unittest.main()
