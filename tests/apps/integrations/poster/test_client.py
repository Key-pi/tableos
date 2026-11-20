"""The Poster API client: requests, errors and the OAuth code exchange (PQ-058, step 1).

No network: every response comes from ``httpx.MockTransport``.
"""

import json
from urllib.parse import parse_qs

import httpx
from django.test import SimpleTestCase

from apps.integrations.poster.client import PosterClient, PosterError, exchange_code

TOKEN = "100200:token-placeholder"
SECRET = "secret-placeholder"


def _client(handler) -> PosterClient:
    return PosterClient(TOKEN, transport=httpx.MockTransport(handler))


class CallTests(SimpleTestCase):
    def test_a_read_goes_to_the_method_with_the_token(self):
        seen = {}

        def handler(request):
            seen["request"] = request
            return httpx.Response(200, json={"response": [{"spot_id": "1"}]})

        result = _client(handler).call("spots.getSpots")

        request = seen["request"]
        self.assertEqual(result, [{"spot_id": "1"}])
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url).split("?")[0], "https://joinposter.com/api/spots.getSpots")
        self.assertEqual(request.url.params["token"], TOKEN)
        self.assertEqual(request.url.params["format"], "json")

    def test_parameters_go_into_the_query(self):
        seen = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"response": {"incoming_order_id": 4}})

        _client(handler).call("incomingOrders.getIncomingOrder", params={"incoming_order_id": 4})

        self.assertEqual(seen["params"]["incoming_order_id"], "4")

    def test_a_write_sends_json(self):
        seen = {}

        def handler(request):
            seen["request"] = request
            return httpx.Response(200, json={"response": {"incoming_order_id": 7}})

        body = {"spot_id": 1, "products": [{"product_id": 6, "modificator_id": 2, "count": 1}]}
        result = _client(handler).call("incomingOrders.createIncomingOrder", body=body)

        request = seen["request"]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["content-type"], "application/json")
        self.assertEqual(json.loads(request.content), body)
        self.assertEqual(result, {"incoming_order_id": 7})

    def test_a_method_name_cannot_change_the_address(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"response": []})

        client = _client(handler)
        for bad in ("../auth", "spots.getSpots?x=1", "spots/getSpots", "", "spots."):
            with self.subTest(method=bad), self.assertRaises(ValueError):
                client.call(bad)
        self.assertEqual(calls, [])


class TokenStaysOutOfLogsTests(SimpleTestCase):
    def test_a_request_is_not_logged_with_its_address(self):
        # httpx logs every request's full URL at INFO; the token is in it.
        def handler(request):
            return httpx.Response(200, json={"response": []})

        with self.assertNoLogs(level="INFO"):
            _client(handler).call("spots.getSpots")


class ErrorTests(SimpleTestCase):
    def _error(self, handler) -> PosterError:
        with self.assertRaises(PosterError) as caught:
            _client(handler).call("incomingOrders.createIncomingOrder", body={"spot_id": 1})
        return caught.exception

    def test_a_rejected_order_is_a_final_error_with_poster_code(self):
        # Step 0: a placeholder phone came back as HTTP 422, error 155.
        error = self._error(lambda request: httpx.Response(
            422, json={"error": 155, "message": "invalid field: Client phone"},
        ))

        self.assertEqual(error.code, 155)
        self.assertEqual(error.http_status, 422)
        self.assertFalse(error.temporary)
        self.assertIn("Client phone", error.message)

    def test_an_error_inside_a_successful_answer_is_an_error(self):
        # Step 0: an unknown incoming order — HTTP 200 with error 42.
        error = self._error(lambda request: httpx.Response(
            200, json={"error": 42, "message": "incoming_order_id is undefined"},
        ))

        self.assertEqual(error.code, 42)
        self.assertFalse(error.temporary)

    def test_a_nested_error_is_read_too(self):
        # Step 0: a method the application may not call.
        error = self._error(lambda request: httpx.Response(200, json={
            "error": {
                "code": 10,
                "message": "Application doesn’t have rights to access this method",
            },
        }))

        self.assertEqual(error.code, 10)
        self.assertIn("rights", error.message)
        self.assertFalse(error.temporary)

    def test_server_trouble_and_limits_are_temporary(self):
        for status in (500, 502, 503, 429):
            with self.subTest(status=status):
                error = self._error(
                    lambda request, s=status: httpx.Response(s, text="<html>busy</html>"),
                )
                self.assertTrue(error.temporary)
                self.assertEqual(error.http_status, status)

    def test_network_trouble_is_temporary(self):
        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)

        def refused(request):
            raise httpx.ConnectError("connection refused", request=request)

        for handler in (timeout, refused):
            with self.subTest(handler=handler.__name__):
                error = self._error(handler)
                self.assertTrue(error.temporary)
                self.assertIsNone(error.http_status)

    def test_a_page_instead_of_json_is_a_final_error(self):
        error = self._error(lambda request: httpx.Response(403, text="<html>blocked</html>"))

        self.assertFalse(error.temporary)
        self.assertEqual(error.http_status, 403)

    def test_an_answer_without_response_is_an_error(self):
        error = self._error(lambda request: httpx.Response(200, json={"something": "else"}))

        self.assertFalse(error.temporary)

    def test_errors_never_carry_the_token(self):
        def timeout(request):
            raise httpx.ReadTimeout(f"timed out {request.url}", request=request)

        handlers = (
            timeout,
            lambda request: httpx.Response(500, text=str(request.url)),
            lambda request: httpx.Response(422, json={"error": 1, "message": str(request.url)}),
        )
        for handler in handlers:
            with self.subTest(handler=handler):
                error = self._error(handler)
                for text in (str(error), repr(error), error.message):
                    self.assertNotIn("token-placeholder", text)
                # No chained exception that could carry the request's URL.
                self.assertIsNone(error.__cause__)
                self.assertTrue(error.__context__ is None or error.__suppress_context__)


class ExchangeCodeTests(SimpleTestCase):
    def _exchange(self, handler, account="venue"):
        return exchange_code(
            account=account,
            code="code-placeholder",
            application_id="5611",
            application_secret=SECRET,
            redirect_uri="https://shop.example.com/integrations/poster/oauth/callback/",
            transport=httpx.MockTransport(handler),
        )

    def test_the_code_is_exchanged_at_the_venues_account(self):
        seen = {}

        def handler(request):
            seen["request"] = request
            return httpx.Response(200, json={
                "access_token": TOKEN, "account_number": "100200", "tariff": {"key": "business"},
                "user": {"id": 1}, "ownerInfo": {},
            })

        result = self._exchange(handler)

        request = seen["request"]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), "https://venue.joinposter.com/api/v2/auth/access_token")
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        self.assertEqual(form, {
            "application_id": "5611",
            "application_secret": SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": "https://shop.example.com/integrations/poster/oauth/callback/",
            "code": "code-placeholder",
        })
        self.assertEqual(result["access_token"], TOKEN)
        self.assertEqual(result["account_number"], "100200")

    def test_a_strange_account_is_refused_before_any_request(self):
        # The account comes from the callback's query: it must not send our
        # secret to another host.
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"access_token": TOKEN})

        for bad in ("evil.com/x?", "evil.com#", "venue.joinposter.com", "Venue", "", "a b"):
            with self.subTest(account=bad), self.assertRaises(ValueError):
                self._exchange(handler, account=bad)
        self.assertEqual(calls, [])

    def test_a_refused_code_is_an_error_without_the_secret(self):
        def handler(request):
            return httpx.Response(400, json={
                "error": 33, "message": f"bad code for {request.content.decode()}",
            })

        with self.assertRaises(PosterError) as caught:
            self._exchange(handler)

        error = caught.exception
        self.assertEqual(error.code, 33)
        self.assertFalse(error.temporary)
        for text in (str(error), repr(error), error.message):
            self.assertNotIn(SECRET, text)
            self.assertNotIn("code-placeholder", text)

    def test_an_answer_without_a_token_is_an_error(self):
        with self.assertRaises(PosterError):
            self._exchange(lambda request: httpx.Response(200, json={"account_number": "1"}))
