import aiohttp
from typing import *
from asyncio import sleep
from . import logger
from ujson import dumps


class ServerOffline(Exception):
    pass

class NodeInstance:
    def __init__(self, url: str):
        self.url: str = url
        self.session = aiohttp.ClientSession(headers={'Accept': 'application/json'}, json_serialize=dumps)
        self.status: bool = False
        self.dispatch = logger.dispatch
    
    async def set_online(self):
        if self.status:
            return
        self.status = True
        await self.dispatch('node_online', self.url)
    
    async def set_offline(self):
        if not self.status:
            return
        self.status = False
        await self.dispatch('node_offline', self.url)

    async def check_alive(self) -> bool:
        try:
            async with self.session.get(f'{self.url}/eth/v1/node/health') as resp:
                if resp.status == 200:
                    await self.set_online()
                    return True
        except:
            await self.set_offline()
            return False
    
    async def do_request(self, method: str, path: str, data: Dict[str, Any]=None) -> Tuple[Optional[Dict[str, Any]], int]:
        async with self.session.request(method, f'{self.url}{path}', json=data) as resp:
            try:
                return ((await resp.text()), resp.status)
            except (aiohttp.ServerTimeoutError, aiohttp.ServerConnectionError):
                await self.set_offline()
                return ServerOffline('Server is offline')
            except:
                return (dumps({'error': 'Server returned unexpected value'}), 500)
    
    async def do_stream_request(self, path: str, data: Dict[str, Any]=None):
        async with self.session.get(f'{self.url}{path}', headers={'accept': 'text/event-stream'}) as resp:
            async for x in resp.content:
                

    async def stop(self):
        await self.session.close()

class OutOfAliveNodes:
    pass

class NodeRouter:
    def __init__(self, urls: List[str]):
        if not urls:
            raise ValueError('No nodes provided')
        self.urls = urls
        self.dispatch = logger.dispatch
        self.listener = logger.listener
    
    async def recheck(self) -> None:
        for node in self.nodes:
            await node.check_alive()
    
    async def repeat_check(self) -> None:
        while True:
            await self.recheck()
            await sleep(60)

    async def setup(self) -> None:
        self.nodes: List[NodeInstance] = [NodeInstance(url) for url in self.urls]
        await self.recheck()
        await self.dispatch('node_router_online')
    
    async def get_alive_node(self) -> Optional[NodeInstance]:
        for node in self.nodes:
            if node.status:
                if await node.check_alive():
                    return node
        return None
    
    async def do_request(self, method: str, path: str, request: Dict[str, Any]=None) -> Tuple[Optional[Dict[str, Any]], int]:
        node = await self.get_alive_node()
        try:
            return await node.do_request(method, path, request)
        except ServerOffline:
            return ServerOffline()
        except AttributeError:
            return OutOfAliveNodes() # you're out of nodes
    
    async def route(self, method: str, path: str, request: Dict[str, Any]=None) -> Tuple[Dict[str, Any], int]:
        data = await self.do_request(method, path, request)

        if isinstance(data, OutOfAliveNodes):
            return (dumps({'error': 'No available nodes'}), 503)

        while isinstance(data, ServerOffline):
            await self.recheck()
            data = await self.do_request(method, path, request)
        return (data[0], data[1])
            
    async def stop(self) -> None:
        for node in self.nodes:
            await node.stop()