"""Bounded native public pages, before any SDK row filtering or output slicing."""
from fastapi import HTTPException


async def native_history_page(client, client_id, market, instrument, since, limit, cap, clock):
    if client_id in {'binanceusdm', 'binancecoinm'}:
        query = {'symbol': market['id'], 'limit': limit}
        if since is not None:
            query['startTime'] = since
        method = client.fapiPublicGetFundingRate if client_id == 'binanceusdm' else client.dapiPublicGetFundingRate
        response = await method(query)
        received = clock()
        rows = response
    elif client_id == 'okx':
        query = {'instId': market['id'], 'limit': limit}
        if since is not None:
            query['before'] = max(since-1, 0)
        response = await client.publicGetPublicFundingRateHistory(query)
        received = clock()
        rows = response.get('data') if isinstance(response, dict) and response.get('code') == '0' else None
    elif client_id == 'bybit':
        query = {'symbol': market['id'], 'category': 'linear' if instrument.linear else 'inverse', 'limit': limit}
        if since is not None:
            query.update(startTime=since, endTime=clock())
        response = await client.publicGetV5MarketFundingHistory(query)
        received = clock()
        result = response.get('result') if isinstance(response, dict) and response.get('retCode') == 0 else None
        rows = result.get('list') if isinstance(result, dict) and result.get('category', query['category']) == query['category'] else None
    elif client_id == 'gate':
        query = {'settle': instrument.settle.lower(), 'contract': market['id'], 'limit': limit}
        if since is not None:
            query['from'] = since // 1000
        response = await client.publicFuturesGetSettleFundingRate(query)
        received = clock()
        rows = response
    elif client_id == 'bitget':
        product = {'USDT': 'USDT-FUTURES', 'USDC': 'USDC-FUTURES'}.get(instrument.settle, 'COIN-FUTURES')
        response = await client.publicMixGetV2MixMarketHistoryFundRate(
            {'symbol': market['id'], 'productType': product, 'pageNo': 1, 'pageSize': limit})
        received = clock()
        rows = response.get('data') if isinstance(response, dict) and response.get('code') == '00000' else None
    elif client_id == 'htx':
        response = await client.contractPublicGetSwapApiV1SwapHistoricalFundingRate(
            {'contract_code': market['id'], 'page_index': 1, 'page_size': limit})
        received = clock()
        data = response.get('data') if isinstance(response, dict) and response.get('status') == 'ok' else None
        rows = data.get('data') if isinstance(data, dict) and data.get('current_page') == 1 else None
    else:
        raise HTTPException(501, 'Native public history page not reviewed')
    if not isinstance(rows, list) or len(rows) > cap:
        raise HTTPException(502, 'Malformed or oversized native history page; nothing saved')
    return rows, received
