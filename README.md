# crewai-tool-agentwallet

Non-custodial wallet tools for [CrewAI](https://github.com/joaomdmoura/crewAI) agents.

Give your CrewAI agents a real wallet: check balances, send tokens, handle HTTP 402 micropayments, and enforce on-chain spend limits — without ever surrendering custody of your keys.

## Features

- **EVM balance checks** — native ETH and any ERC-20 token
- **Token transfers** — sign and broadcast transactions locally (non-custodial)
- **x402 payments** — automatic handling of HTTP 402 Payment Required flows
- **On-chain spend limits** — query AgentAccountV2 contract limits before spending

## Installation

```bash
pip install crewai-tool-agentwallet
```

## Quick Start

```python
from crewai import Agent, Task, Crew
from crewai_tool_agentwallet import AgentWalletToolkit

toolkit = AgentWalletToolkit(
    wallet_address="0xYourAgentWalletAddress",
    private_key="0xYourPrivateKey",
    chain_id=8453,          # Base mainnet
    rpc_url="https://mainnet.base.org",
)

wallet_agent = Agent(
    role="Blockchain Wallet Manager",
    goal="Manage on-chain assets and execute transactions securely",
    backstory=(
        "You are an expert blockchain agent with a non-custodial wallet. "
        "You can check balances, send tokens, and pay for services autonomously."
    ),
    tools=toolkit.get_tools(),
    verbose=True,
)

check_balance_task = Task(
    description="Check the ETH balance of address 0x...",
    agent=wallet_agent,
    expected_output="The current ETH balance in human-readable format",
)

crew = Crew(agents=[wallet_agent], tasks=[check_balance_task])
result = crew.kickoff()
print(result)
```

## Available Tools

### `WalletBalanceTool`

Check native ETH or ERC-20 token balance.

**Input:** `address` (wallet address), optional `token` (ERC-20 contract address)

### `WalletTransferTool`

Sign and broadcast an ETH or ERC-20 transfer.

**Input:** `to` (recipient), `amount_wei`, optional `token`, `gas_limit`

### `X402PaymentTool`

Automatically handle [HTTP 402](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/402) payment flows.

**Input:** `url`, optional `max_amount_usd` (default 1.0), `method`, `body`

### `GetSpendLimitsTool`

Query on-chain spend limits from an AgentAccountV2 contract.

**Input:** `contract_address`, optional `token` (zero address for ETH)

## Using Individual Tools

```python
from crewai_tool_agentwallet import WalletBalanceTool, WalletTransferTool

balance_tool = WalletBalanceTool(
    rpc_url="https://mainnet.base.org",
    wallet_address="0x...",
)

transfer_tool = WalletTransferTool(
    rpc_url="https://mainnet.base.org",
    wallet_address="0x...",
    private_key="0x...",
    chain_id=8453,
)
```

## Security Notes

- Keys never leave your environment — all signing is local
- Use environment variables for `PRIVATE_KEY`, never hardcode
- Use `GetSpendLimitsTool` to enforce budget caps before large transactions

## License

MIT

## Links

- [agentwallet-sdk on npm](https://www.npmjs.com/package/agentwallet-sdk)
- [AI Agent Economy](https://ai-agent-economy.com)
- [GitHub](https://github.com/agentwallet-sdk/crewai-tool-agentwallet)
