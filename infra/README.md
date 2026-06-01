# Infrastructure

Minimal Bicep that creates a fresh resource group with every resource
the examples in this repo need.

## Deploy

```bash
az deployment sub create \
    --name acas-toolkit-bootstrap \
    --location westus2 \
    --template-file infra/main.bicep \
    --parameters infra/main.parameters.json \
    --parameters environmentName=dev
```

Or with `azd`:

```bash
azd up
```

## What gets created

| Resource | Why | Gated by |
|---|---|---|
| Sandbox group (`Microsoft.App/sandboxGroups`) | The ACA sandbox pool your code leases from | always |
| Storage account | Backs workspace volumes + session snapshots | always |
| User-assigned managed identity | For future RBAC / managed-identity scenarios (examples auth as the user) | always |
| Log Analytics workspace + Application Insights | OTel exporter target for examples 06 / 07 | `enableObservability` (default `true`) |
| Foundry account + project + `gpt-5-mini` model deployment | The model example 08 talks to | `enableFoundry` (default `true`) |

Key parameters (see `infra/main.parameters.json`):

* `environmentName` — drives the RG name (`rg-acas-<env>`) and resource suffixes.
* `location` — primary region (default `westus2`). Hosts sandbox group, storage, identity, observability.
* `foundryLocation` — Foundry region (default `eastus2`). Decoupled because `gpt-5-mini` is not in every region's OpenAI catalog.
* `foundryModelName` / `foundryModelVersion` / `foundryModelSkuName` / `foundryModelCapacity` — model deployment knobs.
* `enableObservability` / `enableFoundry` — toggle the optional sections.

## Outputs → `.env`

Copy `.env.example` to `.env` at the repo root and fill it in from the
deployment outputs:

| `.env` variable | Comes from |
|---|---|
| `ACAS_SUBSCRIPTION_ID` | The subscription you deployed into |
| `ACAS_RESOURCE_GROUP` | `rg-acas-<environmentName>` |
| `ACAS_LOCATION` | The `location` parameter (default `westus2`) |
| `ACAS_SANDBOX_GROUP` | Output `sandboxGroupName` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Output `appInsightsConnectionString` (only if `enableObservability=true`) |
| `AZURE_AI_PROJECT_ENDPOINT` | Output `foundryProjectEndpoint` (only if `enableFoundry=true`) |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Output `foundryModelDeploymentName` |

## Tear down

```bash
az group delete -n rg-acas-dev --yes --no-wait
```
