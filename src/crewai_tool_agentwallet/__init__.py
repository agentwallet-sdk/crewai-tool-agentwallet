"""
crewai-tool-agentwallet
=======================
Non-custodial wallet tools for CrewAI agents.
Supports EVM chains, x402 payments, 17-chain CCTP bridge, and
on-chain spend limits via AgentAccountV2.
"""
from __future__ import annotations

import json
from typing import Any, Optional, Type

import httpx
from pydantic import BaseModel, Field

try:
    from crewai.tools import BaseTool
except ImportError as e:
    raise ImportError(
        "crewai is required. Install with: pip install crewai>=0.1.0"
    ) from e

try:
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware
except ImportError as e:
    raise ImportError(
        "web3 is required. Install with: pip install web3>=6.0.0"
    ) from e

# ---------------------------------------------------------------------------
# AgentAccountV2 ABI — spend-limit fragments
# ---------------------------------------------------------------------------
_AGENT_ACCOUNT_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "token", "type": "address"}],
        "name": "getSpendLimit",
        "outputs": [
            {"internalType": "uint256", "name": "limit", "type": "uint256"},
            {"internalType": "uint256", "name": "spent", "type": "uint256"},
            {"internalType": "uint256", "name": "resetAt", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

CCTP_DOMAINS: dict[int, int] = {
    1: 0, 10: 2, 42161: 3, 8453: 6, 137: 7, 43114: 1,
}

# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

class BalanceInput(BaseModel):
    address: str = Field(description="Wallet address")
    token: Optional[str] = Field(default=None, description="ERC-20 token address (omit for native ETH)")


class TransferInput(BaseModel):
    to: str = Field(description="Recipient address")
    amount_wei: int = Field(description="Amount in wei")
    token: Optional[str] = Field(default=None, description="ERC-20 token address (omit for ETH)")
    gas_limit: Optional[int] = Field(default=None, description="Gas limit override")


class X402Input(BaseModel):
    url: str = Field(description="URL that returned HTTP 402")
    max_amount_usd: float = Field(default=1.0, description="Max USD willing to pay")
    method: str = Field(default="GET", description="HTTP method")
    body: Optional[str] = Field(default=None, description="Request body JSON")


class SpendLimitsInput(BaseModel):
    contract_address: str = Field(description="AgentAccountV2 contract address")
    token: str = Field(
        default="0x0000000000000000000000000000000000000000",
        description="Token address (zero for ETH)",
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class WalletBalanceTool(BaseTool):
    name: str = "wallet_balance"
    description: str = (
        "Check the ETH or ERC-20 token balance of a wallet address. "
        "Input: JSON with 'address' (required) and optional 'token' (ERC-20 contract address)."
    )
    args_schema: Type[BaseModel] = BalanceInput

    # Config injected via constructor
    _rpc_url: str
    _wallet_address: str

    def __init__(self, rpc_url: str, wallet_address: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_rpc_url", rpc_url)
        object.__setattr__(self, "_wallet_address", wallet_address)

    def _run(self, address: str, token: Optional[str] = None) -> str:  # type: ignore[override]
        w3 = Web3(Web3.HTTPProvider(self._rpc_url))
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        addr = Web3.to_checksum_address(address or self._wallet_address)

        if token:
            erc20_abi = [
                {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
                 "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
                {"inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}],
                 "stateMutability": "view", "type": "function"},
                {"inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}],
                 "stateMutability": "view", "type": "function"},
            ]
            contract = w3.eth.contract(address=Web3.to_checksum_address(token), abi=erc20_abi)
            balance = contract.functions.balanceOf(addr).call()
            try:
                decimals = contract.functions.decimals().call()
                symbol = contract.functions.symbol().call()
            except Exception:
                decimals, symbol = 18, "TOKEN"
            return f"{balance / 10**decimals:.6f} {symbol} ({balance} raw)"
        else:
            wei = w3.eth.get_balance(addr)
            return f"{Web3.from_wei(wei, 'ether'):.6f} ETH ({wei} wei)"


class WalletTransferTool(BaseTool):
    name: str = "wallet_transfer"
    description: str = (
        "Sign and broadcast an ETH or ERC-20 token transfer from the agent wallet. "
        "Input: JSON with 'to', 'amount_wei', optional 'token' and 'gas_limit'."
    )
    args_schema: Type[BaseModel] = TransferInput

    _rpc_url: str
    _wallet_address: str
    _private_key: str
    _chain_id: int

    def __init__(
        self, rpc_url: str, wallet_address: str, private_key: str, chain_id: int, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_rpc_url", rpc_url)
        object.__setattr__(self, "_wallet_address", wallet_address)
        object.__setattr__(self, "_private_key", private_key)
        object.__setattr__(self, "_chain_id", chain_id)

    def _run(  # type: ignore[override]
        self,
        to: str,
        amount_wei: int,
        token: Optional[str] = None,
        gas_limit: Optional[int] = None,
    ) -> str:
        w3 = Web3(Web3.HTTPProvider(self._rpc_url))
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        sender = Web3.to_checksum_address(self._wallet_address)
        recipient = Web3.to_checksum_address(to)
        nonce = w3.eth.get_transaction_count(sender)
        gas_price = w3.eth.gas_price

        if token:
            erc20_abi = [
                {"inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
                 "name": "transfer", "outputs": [{"name": "", "type": "bool"}],
                 "stateMutability": "nonpayable", "type": "function"}
            ]
            contract = w3.eth.contract(address=Web3.to_checksum_address(token), abi=erc20_abi)
            tx = contract.functions.transfer(recipient, amount_wei).build_transaction({
                "chainId": self._chain_id,
                "gas": gas_limit or 100_000,
                "gasPrice": gas_price,
                "nonce": nonce,
                "from": sender,
            })
        else:
            tx = {
                "to": recipient,
                "value": amount_wei,
                "gas": gas_limit or 21_000,
                "gasPrice": gas_price,
                "nonce": nonce,
                "chainId": self._chain_id,
            }

        signed = w3.eth.account.sign_transaction(tx, self._private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        return f"Transaction sent: {tx_hash.hex()}"


class X402PaymentTool(BaseTool):
    name: str = "x402_payment"
    description: str = (
        "Handle HTTP 402 Payment Required flows automatically. "
        "Signs a micropayment and retries the request. "
        "Input: JSON with 'url', optional 'max_amount_usd' (default 1.0), 'method', 'body'."
    )
    args_schema: Type[BaseModel] = X402Input

    _rpc_url: str
    _wallet_address: str
    _private_key: str
    _chain_id: int

    def __init__(
        self, rpc_url: str, wallet_address: str, private_key: str, chain_id: int, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_rpc_url", rpc_url)
        object.__setattr__(self, "_wallet_address", wallet_address)
        object.__setattr__(self, "_private_key", private_key)
        object.__setattr__(self, "_chain_id", chain_id)

    def _run(  # type: ignore[override]
        self,
        url: str,
        max_amount_usd: float = 1.0,
        method: str = "GET",
        body: Optional[str] = None,
    ) -> str:
        import asyncio

        async def _async_run() -> str:
            w3 = Web3(Web3.HTTPProvider(self._rpc_url))
            async with httpx.AsyncClient(timeout=30) as client:
                req_kwargs: dict[str, Any] = {"method": method, "url": url}
                if body:
                    req_kwargs["content"] = body.encode()
                response = await client.request(**req_kwargs)

                if response.status_code != 402:
                    return f"Response {response.status_code}: {response.text[:500]}"

                x402_details = response.headers.get("X-Payment-Details", "{}")
                try:
                    payment_info = json.loads(x402_details)
                except json.JSONDecodeError:
                    payment_info = {}

                amount = payment_info.get("amount", "unknown")
                currency = payment_info.get("currency", "USDC")
                payee = payment_info.get("payee", "unknown")

                try:
                    if float(amount) > max_amount_usd:
                        return (
                            f"Payment declined: {amount} {currency} exceeds max {max_amount_usd} USD"
                        )
                except (ValueError, TypeError):
                    pass

                message = f"x402-payment:{url}:{amount}:{currency}:{payee}"
                msg_hash = w3.keccak(text=message)
                signed = w3.eth.account.sign_message(
                    w3.eth.account._sign_hash(msg_hash), private_key=self._private_key
                )
                req_kwargs["headers"] = {
                    "X-Payment": f"x402 signature={signed.signature.hex()},payer={self._wallet_address}"
                }
                paid = await client.request(**req_kwargs)
                return f"Payment sent ({amount} {currency} to {payee}). Response {paid.status_code}: {paid.text[:500]}"

        return asyncio.run(_async_run())


class GetSpendLimitsTool(BaseTool):
    name: str = "get_spend_limits"
    description: str = (
        "Query on-chain spend limits from an AgentAccountV2 contract. "
        "Input: JSON with 'contract_address' and optional 'token' (zero address for ETH)."
    )
    args_schema: Type[BaseModel] = SpendLimitsInput

    _rpc_url: str

    def __init__(self, rpc_url: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_rpc_url", rpc_url)

    def _run(  # type: ignore[override]
        self,
        contract_address: str,
        token: str = "0x0000000000000000000000000000000000000000",
    ) -> str:
        w3 = Web3(Web3.HTTPProvider(self._rpc_url))
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(contract_address), abi=_AGENT_ACCOUNT_ABI
        )
        try:
            limit, spent, reset_at = contract.functions.getSpendLimit(
                Web3.to_checksum_address(token)
            ).call()
            remaining = limit - spent
            return f"Limit: {limit} | Spent: {spent} | Remaining: {remaining} | ResetAt: {reset_at}"
        except Exception as exc:
            return f"Error: {exc}"


# ---------------------------------------------------------------------------
# Toolkit
# ---------------------------------------------------------------------------

class AgentWalletToolkit:
    """
    Bundle of all AgentWallet tools for CrewAI agents.

    Example::

        from crewai import Agent, Task, Crew
        from crewai_tool_agentwallet import AgentWalletToolkit

        toolkit = AgentWalletToolkit(
            wallet_address="0x...",
            private_key="0x...",
            chain_id=8453,
            rpc_url="https://mainnet.base.org",
        )

        agent = Agent(
            role="Wallet Manager",
            goal="Manage on-chain assets",
            backstory="Expert at blockchain transactions",
            tools=toolkit.get_tools(),
        )
    """

    def __init__(
        self,
        wallet_address: str,
        private_key: str,
        chain_id: int = 8453,
        rpc_url: str = "https://mainnet.base.org",
    ) -> None:
        self.wallet_address = wallet_address
        self.private_key = private_key
        self.chain_id = chain_id
        self.rpc_url = rpc_url

    def get_tools(self) -> list[BaseTool]:
        return [
            WalletBalanceTool(self.rpc_url, self.wallet_address),
            WalletTransferTool(self.rpc_url, self.wallet_address, self.private_key, self.chain_id),
            X402PaymentTool(self.rpc_url, self.wallet_address, self.private_key, self.chain_id),
            GetSpendLimitsTool(self.rpc_url),
        ]


__all__ = [
    "AgentWalletToolkit",
    "WalletBalanceTool",
    "WalletTransferTool",
    "X402PaymentTool",
    "GetSpendLimitsTool",
]
