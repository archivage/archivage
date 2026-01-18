"""
Twitter GraphQL API client for archiving.
"""

import json
import time
import random
from datetime import datetime
import httpx
from pathlib import Path
from http.cookiejar import MozillaCookieJar
from .transaction import TransactionId
from .log import logger


# Twitter's public bearer token (used by web client)
BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# GraphQL endpoints
ENDPOINTS = {
    "user_tweets": "/graphql/E8Wq-_jFSaU7hxVcuOPR9g/UserTweets",
    "user_by_screen_name": "/graphql/ck5KkZ8t5cOmoLssopN99Q/UserByScreenName",
    "likes": "/graphql/TGEKkJG_meudeaFcqaxM-Q/Likes",
    "bookmarks": "/graphql/pLtjrO4ubNh996M_Cubwsg/Bookmarks",
    "search": "/graphql/4fpceYZ6-YQCx_JSl_Cn_A/SearchTimeline",
}

# Features for UserByScreenName
FEATURES_USER = {
    "hidden_profile_subscriptions_enabled": True,
    "payments_enabled": False,
    "rweb_xchat_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "verified_phone_label_enabled": False,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}

# Features for pagination requests (UserTweets, Likes, etc.)
FEATURES_PAGINATION = {
    "rweb_video_screen_enabled": False,
    "payments_enabled": False,
    "rweb_xchat_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_grok_analysis_button_from_backend": True,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
}


class TwitterClient:
    """HTTP client for Twitter GraphQL API."""

    def __init__(self, cookies_path: Path):
        self.root = "https://x.com/i/api"
        self.cookies_path = cookies_path
        self.client = None
        self.csrf_token = None
        self.transaction = None

    def _loadCookies(self) -> dict[str, str]:
        """Load cookies from Netscape format file."""
        jar = MozillaCookieJar(self.cookies_path)
        jar.load(ignore_discard=True, ignore_expires=True)
        return {c.name: c.value for c in jar if c.domain.endswith("x.com")}

    def _initClient(self):
        """Initialize HTTP client with auth headers."""
        cookies = self._loadCookies()
        self.csrf_token = cookies.get("ct0", "")

        # Generate csrf token if not in cookies
        if not self.csrf_token:
            import secrets
            self.csrf_token = secrets.token_hex(16)
            cookies["ct0"] = self.csrf_token

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
            "Accept": "*/*",
            "Referer": "https://x.com/",
            "authorization": f"Bearer {BEARER_TOKEN}",
            "content-type": "application/json",
            "x-csrf-token": self.csrf_token,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        self.client = httpx.Client(
            headers=headers,
            cookies=cookies,
            timeout=30.0,
            follow_redirects=True,
        )

        self._initTransaction()

    def _initTransaction(self):
        """Initialize transaction ID generator."""
        homepage_client = httpx.Client(
            cookies=dict(self.client.cookies),
            timeout=30.0,
            follow_redirects=True,
        )
        try:
            logger.debug("GET https://x.com/ (homepage)")
            resp = homepage_client.get("https://x.com/")
            logger.debug(f"Homepage: {resp.status_code}, {len(resp.text)} bytes")

            # Transfer cookies from homepage response
            for name, value in resp.cookies.items():
                self.client.cookies.set(name, value)

            # Initialize transaction ID generator
            self.transaction = TransactionId()
            def fetchJs(url):
                logger.debug(f"GET {url[:80]}")
                text = self.client.get(url).text
                logger.debug(f"JS: {len(text)} bytes")
                return text
            self.transaction.initialize(resp.text, fetchJs)
            logger.debug("Transaction ID initialized")

            # Update csrf token if changed
            if "ct0" in self.client.cookies:
                self.csrf_token = self.client.cookies["ct0"]
                self.client.headers["x-csrf-token"] = self.csrf_token

        except Exception as e:
            logger.warning(f"Transaction ID init failed: {e}")
            self.transaction = None
        finally:
            homepage_client.close()

    def _getTransactionId(self, url: str, method: str = "GET") -> str | None:
        """Generate transaction ID for request."""
        if self.transaction is None:
            return None
        path = url[url.find("/", 8):]  # Extract path from URL
        return self.transaction.generate(method, path)

    def _call(self, endpoint: str, params: dict) -> dict:
        """Make API request with retry logic."""
        if self.client is None:
            self._initClient()

        url = self.root + endpoint
        max_retries = 5

        for attempt in range(max_retries):
            txn_id = self._getTransactionId(url)
            headers = {}
            if txn_id:
                headers["x-client-transaction-id"] = txn_id

            logger.debug(f"GET {endpoint}")
            response = self.client.get(url, params=params, headers=headers)
            logger.debug(f"Response: {response.status_code}")

            # Update csrf token if changed
            if "ct0" in response.cookies:
                self.csrf_token = response.cookies["ct0"]
                self.client.headers["x-csrf-token"] = self.csrf_token

            # Handle rate limiting
            remaining = int(response.headers.get("x-rate-limit-remaining", 100))
            if remaining < 5:
                reset_time = int(response.headers.get("x-rate-limit-reset", 0))
                sleep_time = max(reset_time - time.time(), 60)
                resume_at = datetime.fromtimestamp(reset_time).strftime("%H:%M:%S")
                logger.info(f"Rate limit low ({remaining}), sleeping {sleep_time:.0f}s (until {resume_at})")
                print(f"  Rate limit low ({remaining}), sleeping {sleep_time/60:.1f}m (until {resume_at})", flush=True)
                time.sleep(sleep_time)
                continue

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429:
                sleep_time = 60 * (2 ** attempt) + random.randint(0, 30)
                resume_at = datetime.fromtimestamp(time.time() + sleep_time).strftime("%H:%M:%S")
                logger.warning(f"429 Too Many Requests, sleeping {sleep_time}s (until {resume_at})")
                print(f"  Rate limited (429), sleeping {sleep_time/60:.1f}m (until {resume_at})", flush=True)
                time.sleep(sleep_time)
                continue

            logger.error(f"HTTP {response.status_code}: {response.text[:200]}")
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                raise Exception(f"API error: {response.status_code}")

        raise Exception("Max retries exceeded")

    def getUserId(self, screen_name: str) -> str:
        """Get user ID from screen name."""
        logger.info(f"Looking up @{screen_name}")
        variables = {
            "screen_name": screen_name,
            "withGrokTranslatedBio": False,
        }
        features = FEATURES_USER.copy()
        features["subscriptions_verification_info_is_identity_verified_enabled"] = True
        features["subscriptions_verification_info_verified_since_enabled"] = True

        params = {
            "variables": json.dumps(variables),
            "features": json.dumps(features),
            "fieldToggles": json.dumps({"withAuxiliaryUserLabels": True}),
        }

        data = self._call(ENDPOINTS["user_by_screen_name"], params)
        return data["data"]["user"]["result"]["rest_id"]

    def getUserTweets(self, user_id: str, cursor: str = None, count: int = 100,
                       include_retweets: bool = False, _retry: bool = True):
        """
        Fetch a page of tweets from user timeline.

        Returns (tweets, next_cursor) where next_cursor is None if no more pages.
        """
        variables = {
            "userId": user_id,
            "count": count,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": False,
            "withVoice": True,
        }
        if cursor:
            variables["cursor"] = cursor

        params = {
            "variables": json.dumps(variables),
            "features": json.dumps(FEATURES_PAGINATION),
            "fieldToggles": json.dumps({"withArticlePlainText": False}),
        }

        data = self._call(ENDPOINTS["user_tweets"], params)

        # Parse response - handle both timeline and timeline_v2 keys
        try:
            result = data["data"]["user"]["result"]
            if "timeline_v2" in result:
                instructions = result["timeline_v2"]["timeline"]["instructions"]
            elif "timeline" in result:
                instructions = result["timeline"]["timeline"]["instructions"]
            else:
                raise KeyError("No timeline in response")
        except (KeyError, TypeError) as e:
            logger.error(f"Unexpected response structure: {e}")
            logger.debug(json.dumps(data, indent=2)[:1000])
            if _retry:
                logger.warning("Retrying after malformed response")
                time.sleep(2)
                return self.getUserTweets(user_id, cursor, count, include_retweets, _retry=False)
            return [], None

        tweets = []
        next_cursor = None
        stop_on_empty = True  # Default: stop if no tweets
        instr_types = [i.get("type") for i in instructions]
        logger.debug(f"Instructions: {instr_types}")

        for instr in instructions:
            instr_type = instr.get("type")

            if instr_type == "TimelineAddEntries":
                for entry in instr.get("entries", []):
                    entry_id = entry.get("entryId", "")

                    if entry_id.startswith("tweet-"):
                        content = entry.get("content", {})
                        if "itemContent" in content:
                            tweet_result = content["itemContent"].get(
                                "tweet_results", {}
                            ).get("result")
                            if tweet_result:
                                # Skip retweets unless configured to include
                                if not include_retweets:
                                    legacy = tweet_result.get("legacy", {})
                                    if "retweeted_status_result" in legacy:
                                        continue
                                tweets.append(tweet_result)

                    elif entry_id.startswith("cursor-bottom-"):
                        cursor_content = entry.get("content", {})
                        # Check itemContent wrapper (some responses nest it)
                        if "itemContent" in cursor_content:
                            cursor_content = cursor_content["itemContent"]
                        next_cursor = cursor_content.get("value")
                        stop_on_empty = cursor_content.get("stopOnEmptyResponse", True)
                        logger.debug(f"Cursor from TimelineAddEntries")

            elif instr_type == "TimelineReplaceEntry":
                entry = instr.get("entry", {})
                if entry.get("entryId", "").startswith("cursor-bottom-"):
                    cursor_content = entry.get("content", {})
                    if "itemContent" in cursor_content:
                        cursor_content = cursor_content["itemContent"]
                    next_cursor = cursor_content.get("value")
                    stop_on_empty = cursor_content.get("stopOnEmptyResponse", True)
                    logger.debug(f"Cursor from TimelineReplaceEntry")

        # Detect definitive end of timeline
        # Cursor prefix -1| or 0| indicates we've reached the end
        is_end_cursor = (
            next_cursor and next_cursor.startswith(("-1|", "0|"))
        )

        # Log cursor status
        if next_cursor:
            if is_end_cursor:
                logger.info(f"End cursor received: {next_cursor[:40]}...")
                next_cursor = None  # Signal end of timeline
            else:
                logger.debug(f"Next cursor: {next_cursor[:40]}...")
                if not stop_on_empty:
                    logger.debug("stopOnEmptyResponse=False, will continue even with no tweets")
        elif tweets:
            # Suspicious: got tweets but no cursor — retry once
            if _retry:
                logger.warning(
                    f"Got {len(tweets)} tweets but no cursor — retrying once"
                )
                time.sleep(2)
                return self.getUserTweets(user_id, cursor, count, include_retweets, _retry=False)
            else:
                logger.warning(
                    f"Got {len(tweets)} tweets but no cursor after retry — stopping"
                )
        else:
            logger.debug("No cursor, no tweets — end of timeline")

        return tweets, next_cursor

    def searchTweets(self, query: str, cursor: str = None, count: int = 20,
                     include_retweets: bool = False, _retry: bool = True):
        """
        Search tweets using Twitter's Search API.

        Query examples:
          - "from:username" — tweets from user
          - "from:username max_id:123" — tweets older than ID
          - "from:username since_id:123" — tweets newer than ID

        Returns (tweets, next_cursor) where next_cursor is None if no more pages.
        """
        variables = {
            "rawQuery": query,
            "count": count,
            "querySource": "typed_query",
            "product": "Latest",
        }
        if cursor:
            variables["cursor"] = cursor

        params = {
            "variables": json.dumps(variables),
            "features": json.dumps(FEATURES_PAGINATION),
        }

        data = self._call(ENDPOINTS["search"], params)

        # Check for server errors (e.g., TimeoutError code 29)
        if "errors" in data:
            for err in data["errors"]:
                err_name = err.get("name", "")
                err_code = err.get("code", 0)
                logger.warning(f"Search error: {err_name} (code {err_code})")
                # Retry on server-side errors
                if err.get("source") == "Server" and _retry:
                    logger.info("Server error, retrying after 5s...")
                    time.sleep(5)
                    return self.searchTweets(query, cursor, count, include_retweets, _retry=False)

        # Parse response
        try:
            result = data["data"]["search_by_raw_query"]["search_timeline"]["timeline"]
            instructions = result["instructions"]
        except (KeyError, TypeError) as e:
            logger.error(f"Unexpected search response structure: {e}")
            logger.debug(json.dumps(data, indent=2)[:1000])
            if _retry:
                logger.warning("Retrying after malformed response")
                time.sleep(5)
                return self.searchTweets(query, cursor, count, include_retweets, _retry=False)
            # Signal retriable error (not end of timeline)
            raise Exception(f"Search API error: {e}")

        tweets = []
        next_cursor = None
        instr_types = [i.get("type") for i in instructions]
        logger.debug(f"Search instructions: {instr_types}")

        for instr in instructions:
            instr_type = instr.get("type")

            if instr_type == "TimelineAddEntries":
                for entry in instr.get("entries", []):
                    entry_id = entry.get("entryId", "")

                    if entry_id.startswith("tweet-"):
                        content = entry.get("content", {})
                        if "itemContent" in content:
                            tweet_result = content["itemContent"].get(
                                "tweet_results", {}
                            ).get("result")
                            if tweet_result:
                                # Skip retweets unless configured to include
                                if not include_retweets:
                                    legacy = tweet_result.get("legacy", {})
                                    if "retweeted_status_result" in legacy:
                                        continue
                                tweets.append(tweet_result)

                    elif entry_id.startswith("cursor-bottom-"):
                        cursor_content = entry.get("content", {})
                        if "itemContent" in cursor_content:
                            cursor_content = cursor_content["itemContent"]
                        next_cursor = cursor_content.get("value")
                        logger.debug("Cursor from search TimelineAddEntries")

            elif instr_type == "TimelineReplaceEntry":
                entry = instr.get("entry", {})
                if entry.get("entryId", "").startswith("cursor-bottom-"):
                    cursor_content = entry.get("content", {})
                    if "itemContent" in cursor_content:
                        cursor_content = cursor_content["itemContent"]
                    next_cursor = cursor_content.get("value")
                    logger.debug("Cursor from search TimelineReplaceEntry")

        # Log cursor status
        if next_cursor:
            logger.debug(f"Search next cursor: {next_cursor[:40]}...")
        elif tweets:
            if _retry:
                logger.warning(f"Search got {len(tweets)} tweets but no cursor — retrying")
                time.sleep(2)
                return self.searchTweets(query, cursor, count, include_retweets, _retry=False)
            else:
                logger.warning(f"Search got {len(tweets)} tweets but no cursor — stopping")
        else:
            logger.debug("Search: no cursor, no tweets — end of results")

        return tweets, next_cursor

    def close(self):
        """Close the HTTP client."""
        if self.client:
            self.client.close()
