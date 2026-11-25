import unittest
from unittest.mock import AsyncMock, Mock

from utils.telegram_health import check_connection


class TelegramHealthTests(unittest.IsolatedAsyncioTestCase):
    def client(self, connected=True):
        client = AsyncMock()
        client.is_connected = Mock(return_value=connected)
        return client

    async def test_quiet_but_healthy_connection_stays_connected(self):
        client = self.client()
        await check_connection(client)
        client.assert_awaited_once()
        client.disconnect.assert_not_awaited()
        client.connect.assert_not_awaited()

    async def test_disconnected_client_is_connected_before_probe(self):
        client = self.client(False)
        await check_connection(client)
        client.connect.assert_awaited_once()
        client.assert_awaited_once()

    async def test_failed_rpc_reconnects_and_verifies(self):
        client = self.client()
        client.side_effect = [ConnectionError('disconnected'), object()]
        await check_connection(client)
        client.disconnect.assert_awaited_once()
        client.connect.assert_awaited_once()
        self.assertEqual(client.await_count, 2)

    async def test_failed_reconnection_is_not_reported_healthy(self):
        client = self.client(False)
        client.connect.side_effect = ConnectionError('offline')
        with self.assertRaises(ConnectionError):
            await check_connection(client)
        client.assert_not_awaited()
