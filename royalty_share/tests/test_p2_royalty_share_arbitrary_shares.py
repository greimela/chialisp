from blspy import G2Element
from hashlib import sha256
from pathlib import Path
from typing import Dict, List, Optional

import pytest
import pytest_asyncio

from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.condition_opcodes import ConditionOpcode
from chia.types.coin_spend import CoinSpend
from chia.types.spend_bundle import SpendBundle
from chia.util.ints import uint64
from chia.wallet.cat_wallet.cat_utils import CAT_MOD

import cdv.clibs as std_lib
from cdv.test import CoinWrapper, SpendResult
from cdv.test import setup as setup_test
from cdv.util.load_clvm import load_clvm

clibs_path: Path = Path(std_lib.__file__).parent
ROYALTY_MOD_REST_TO_LAST: Program = load_clvm("p2_royalty_share_arbitrary_shares_rest_to_last.clsp", "clsp.p2_royalty_share_arbitrary_shares", search_paths=[clibs_path])

class TestP2RoyaltyShareAllocatedPercentagesFeeless:

    @pytest_asyncio.fixture(scope="function")
    async def setup(self):
        network, alice, bob = await setup_test()
        await network.farm_block()
        yield network, alice, bob

    @pytest.mark.asyncio
    async def test_puzzle_50_50_no_remainder(self, setup):
        network, alice, bob = setup
        await(network.farm_block(farmer=alice))
        
        f00d = bytes32.fromhex('00000000000000000000000000000000000000000000000000000000cafef00d')
        beef = bytes32.fromhex('00000000000000000000000000000000000000000000000000000000cafebeef')
        burn = bytes32.fromhex('000000000000000000000000000000000000000000000000000000000000dead')

        try:
            royalty_puzzle: Program = ROYALTY_MOD_REST_TO_LAST.curry([[f00d, 5000], [beef, 5000]])
            royalty_amount = uint64(10000)

            allocation_spend_result = await self.do_royalty_spends(network, alice, royalty_puzzle, royalty_amount)

            allocation_spend_coins = allocation_spend_result["additions"]
            assert 2 == len(allocation_spend_coins)
            allocation_f00d: Coin = None
            allocation_beef: Coin = None

            for coin in allocation_spend_coins:
                if coin.puzzle_hash == f00d:
                    allocation_f00d = coin
                elif coin.puzzle_hash == beef:
                    allocation_beef = coin

            assert allocation_f00d is not None
            assert allocation_f00d.amount == uint64(5000)

            assert allocation_beef is not None
            assert allocation_beef.amount == uint64(5000)

        finally:
            await network.close()    

    @pytest.mark.asyncio
    async def test_puzzle_50_50_remainder_to_last(self, setup):
        network, alice, bob = setup
        await(network.farm_block(farmer=alice))
        
        f00d = bytes32.fromhex('00000000000000000000000000000000000000000000000000000000cafef00d')
        beef = bytes32.fromhex('00000000000000000000000000000000000000000000000000000000cafebeef')

        try:
            royalty_puzzle: Program = ROYALTY_MOD_REST_TO_LAST.curry([[f00d, 5000], [beef, 5000]])
            royalty_amount = uint64(10001)

            allocation_spend_result = await self.do_royalty_spends(network, alice, royalty_puzzle, royalty_amount)

            allocation_spend_coins = allocation_spend_result["additions"]
            assert 2 == len(allocation_spend_coins)
            allocation_f00d: Coin = None
            allocation_beef: Coin = None

            for coin in allocation_spend_coins:
                if coin.puzzle_hash == f00d:
                    allocation_f00d = coin
                elif coin.puzzle_hash == beef:
                    allocation_beef = coin

            assert allocation_f00d is not None
            assert allocation_f00d.amount == uint64(5000)

            assert allocation_beef is not None
            assert allocation_beef.amount == uint64(5000 + 1)

        finally:
            await network.close()    

    @pytest.mark.asyncio
    async def test_puzzle_dusting_to_last(self, setup):
        network, alice, bob = setup
        await(network.farm_block(farmer=alice))
        
        f00d = bytes32.fromhex('00000000000000000000000000000000000000000000000000000000cafef00d')
        beef = bytes32.fromhex('00000000000000000000000000000000000000000000000000000000cafebeef')

        try:
            royalty_puzzle: Program = ROYALTY_MOD_REST_TO_LAST.curry([[f00d, 5000], [beef, 5000]])
            royalty_amount = uint64(1)

            allocation_spend_result = await self.do_royalty_spends(network, alice, royalty_puzzle, royalty_amount)

            allocation_spend_coins = allocation_spend_result["additions"]
            assert 2 == len(allocation_spend_coins)
            
            allocation_f00d: Coin = None
            allocation_beef: Coin = None

            for coin in allocation_spend_coins:
                if coin.puzzle_hash == f00d:
                    allocation_f00d = coin
                elif coin.puzzle_hash == beef:
                    allocation_beef = coin

            assert allocation_f00d is not None
            assert allocation_f00d.amount == uint64(0)

            assert allocation_beef is not None
            assert allocation_beef.amount == uint64(1)


        finally:
            await network.close()    

    async def do_royalty_spends(self, network, alice, royalty_puzzle, royalty_amount):
        royalty_puzzle_hash = royalty_puzzle.get_tree_hash()

        #send funds to royalty puzzle, emulating the sale of an NFT
        royalty_coin: Optional[CoinWrapper] = await alice.choose_coin(royalty_amount)
        royalty_spend_result: SpendResult = await alice.spend_coin(royalty_coin, 
            pushTx=True,
            amt=royalty_amount,
            custom_conditions=[
                [
                    ConditionOpcode.CREATE_COIN,
                    royalty_puzzle_hash,
                    royalty_amount
                ],
                [
                    ConditionOpcode.CREATE_COIN,
                    royalty_coin.puzzle_hash,
                    (royalty_coin.amount - royalty_amount)
                ]
            ])

        assert royalty_spend_result.error is None
        assert len(royalty_spend_result.outputs) == 2
        royalty_coin_to_spend = None
        for coin in royalty_spend_result.outputs:
            if coin.puzzle_hash == royalty_puzzle_hash:
                royalty_coin_to_spend = coin
        assert royalty_coin_to_spend.puzzle_hash == royalty_puzzle_hash
 
        mod, curried_args = royalty_puzzle.uncurry()
        if mod == CAT_MOD:
            mod, curried_args = curried_args.at("rrf").uncurry()
        payout_scheme = curried_args.first()

        total_shares = 0
        for entry in payout_scheme.as_iter():
            total_shares += entry.rest().first().as_int()
        
        allocation_spend = CoinSpend(
            royalty_coin_to_spend,
            royalty_puzzle,
            Program.to([royalty_amount, total_shares])
        )
        allocation_spend_bundle = SpendBundle(
            [allocation_spend],
            G2Element(),
        )

        allocation_spend_result = await network.push_tx(allocation_spend_bundle)
        assert "error" not in allocation_spend_result

        return allocation_spend_result

    @pytest.mark.asyncio
    async def test_three_ways_uneven(self, setup):
        network, alice, bob = setup
        await(network.farm_block(farmer=alice))
        
        f001 = bytes32.fromhex('000000000000000000000000000000000000000000000000000000000000f001')
        f002 = bytes32.fromhex('000000000000000000000000000000000000000000000000000000000000f002')
        f003 = bytes32.fromhex('000000000000000000000000000000000000000000000000000000000000f003')

        try:
            royalty_puzzle: Program = ROYALTY_MOD_REST_TO_LAST.curry([[f001, 8000], [f002, 1500], [f003, 500]])
            royalty_amount = uint64(1000000)

            allocation_spend_result = await self.do_royalty_spends(network, alice, royalty_puzzle, royalty_amount)

            allocation_spend_coins = allocation_spend_result["additions"]
            assert 3 == len(allocation_spend_coins)
            allocation_f001: Coin = None
            allocation_f002: Coin = None
            allocation_f003: Coin = None

            for coin in allocation_spend_coins:
                if coin.puzzle_hash == f001:
                    allocation_f001 = coin
                elif coin.puzzle_hash == f002:
                    allocation_f002 = coin
                elif coin.puzzle_hash == f003:
                    allocation_f003 = coin

            assert allocation_f001 is not None
            assert allocation_f001.amount == uint64(800000)

            assert allocation_f002 is not None
            assert allocation_f002.amount == uint64(150000)

            assert allocation_f003 is not None
            assert allocation_f003.amount == uint64(50000)

        finally:
            await network.close()    

    @pytest.mark.asyncio
    async def test_invalid_amount(self, setup):
        network, alice, bob = setup
        await(network.farm_block(farmer=alice))
        
        f00d = bytes32.fromhex('00000000000000000000000000000000000000000000000000000000cafef00d')
        beef = bytes32.fromhex('00000000000000000000000000000000000000000000000000000000cafebeef')

        try:
            royalty_puzzle: Program = ROYALTY_MOD_REST_TO_LAST.curry([[f00d, 5000], [beef, 5000]])
            royalty_amount = uint64(10000)
            invalid_amount = royalty_amount - uint64(5000)

            royalty_puzzle_hash = royalty_puzzle.get_tree_hash()

            #send funds to royalty puzzle, emulating the sale of an NFT
            royalty_coin: Optional[CoinWrapper] = await alice.choose_coin(royalty_amount)
            royalty_spend_result: SpendResult = await alice.spend_coin(royalty_coin, 
                pushTx=True,
                amt=royalty_amount,
                custom_conditions=[
                    [
                        ConditionOpcode.CREATE_COIN,
                        royalty_puzzle_hash,
                        royalty_amount
                    ],
                    [
                        ConditionOpcode.CREATE_COIN,
                        royalty_coin.puzzle_hash,
                        (royalty_coin.amount - royalty_amount)
                    ]
                ])

            assert royalty_spend_result.error is None
            assert len(royalty_spend_result.outputs) == 2
            royalty_coin_to_spend = None
            for coin in royalty_spend_result.outputs:
                if coin.puzzle_hash == royalty_puzzle_hash:
                    royalty_coin_to_spend = coin
            assert royalty_coin_to_spend.puzzle_hash == royalty_puzzle_hash
    
            allocation_spend = CoinSpend(
                royalty_coin_to_spend,
                royalty_puzzle,
                Program.to([invalid_amount, 10000])
            )
            allocation_spend_bundle = SpendBundle(
                [allocation_spend],
                G2Element(),
            )

            allocation_spend_result = await network.push_tx(allocation_spend_bundle)
            assert "error" in allocation_spend_result

        finally:
            await network.close()   


    @pytest.mark.asyncio
    async def test_invalid_total_shares(self, setup):
        network, alice, bob = setup
        await(network.farm_block(farmer=alice))
        
        f00d = bytes32.fromhex('00000000000000000000000000000000000000000000000000000000cafef00d')
        beef = bytes32.fromhex('00000000000000000000000000000000000000000000000000000000cafebeef')

        try:
            royalty_puzzle: Program = ROYALTY_MOD_REST_TO_LAST.curry([[f00d, 5000], [beef, 5000]])
            royalty_amount = uint64(10000)

            royalty_puzzle_hash = royalty_puzzle.get_tree_hash()

            #send funds to royalty puzzle, emulating the sale of an NFT
            royalty_coin: Optional[CoinWrapper] = await alice.choose_coin(royalty_amount)
            royalty_spend_result: SpendResult = await alice.spend_coin(royalty_coin, 
                pushTx=True,
                amt=royalty_amount,
                custom_conditions=[
                    [
                        ConditionOpcode.CREATE_COIN,
                        royalty_puzzle_hash,
                        royalty_amount
                    ],
                    [
                        ConditionOpcode.CREATE_COIN,
                        royalty_coin.puzzle_hash,
                        (royalty_coin.amount - royalty_amount)
                    ]
                ])

            assert royalty_spend_result.error is None
            assert len(royalty_spend_result.outputs) == 2
            royalty_coin_to_spend = None
            for coin in royalty_spend_result.outputs:
                if coin.puzzle_hash == royalty_puzzle_hash:
                    royalty_coin_to_spend = coin
            assert royalty_coin_to_spend.puzzle_hash == royalty_puzzle_hash
    
            allocation_spend = CoinSpend(
                royalty_coin_to_spend,
                royalty_puzzle,
                Program.to([royalty_amount, 5000])  # correct would be 10000
            )
            allocation_spend_bundle = SpendBundle(
                [allocation_spend],
                G2Element(),
            )

            allocation_spend_result = await network.push_tx(allocation_spend_bundle)
            assert "error" in allocation_spend_result

        finally:
            await network.close()  
