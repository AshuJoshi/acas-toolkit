// =============================================================================
// ACAS Toolkit — main.bicep (subscription scope)
//
// Creates a resource group containing everything any example in this repo
// needs to run end-to-end:
//   * One ACA sandbox group              (the cloud sandbox pool)
//   * One storage account                (backs workspace volumes + snapshots)
//   * One user-assigned managed identity (for future RBAC / managed-identity
//                                         scenarios; not used by the examples
//                                         themselves, which auth as the user)
//   * Log Analytics + App Insights       (examples 06/07; gated by
//                                         enableObservability)
//   * Foundry account + project + model  (example 08; gated by enableFoundry)
//
// SAFETY: only touches the RG it creates. ``azd down`` cannot remove
// anything outside ``rg-acas-${environmentName}``.
// =============================================================================

targetScope = 'subscription'

@minLength(1)
@maxLength(32)
@description('Name of the azd environment. Drives the RG name and resource suffixes.')
param environmentName string

@minLength(1)
@description('Primary Azure region. Hosts the sandbox group, storage, identity, and observability resources.')
param location string = 'westus2'

@minLength(1)
@description('Region for the Foundry account + project + model deployment. Defaults to eastus2 because gpt-5-mini is not yet in the westus2 OpenAI catalog. The Foundry account lives in the same RG as the sandbox group, just in a different region.')
param foundryLocation string = 'eastus2'

@description('Optional override for the resource group name. Default: rg-acas-<environmentName>.')
param resourceGroupName string = ''

@description('Whether to provision Log Analytics + Application Insights for examples 06/07.')
param enableObservability bool = true

@description('Whether to provision a Foundry account + project + model deployment for example 08.')
param enableFoundry bool = true

@description('Foundry model deployment name (used only when enableFoundry=true). Matches the agent example\'s ``AZURE_AI_MODEL_DEPLOYMENT_NAME``.')
param foundryModelName string = 'gpt-5-mini'

@description('Foundry model version. Required by the CognitiveServices API for `gpt-5-*` family models. Leave empty only if Foundry accepts a null version for the chosen model.')
param foundryModelVersion string = '2025-08-07'

@description('Foundry model SKU. ``GlobalStandard`` is the cheapest serverless option for gpt-* models.')
param foundryModelSkuName string = 'GlobalStandard'

@description('Foundry model deployment capacity (TPM units, in thousands). 10 = 10K tokens/min.')
param foundryModelCapacity int = 10

@description('Tag bag applied to every resource.')
param tags object = {
  'azd-env-name': environmentName
  workload: 'acas-toolkit'
}

var effectiveResourceGroupName = empty(resourceGroupName) ? 'rg-acas-${environmentName}' : resourceGroupName
var resourceToken = toLower(uniqueString(subscription().id, environmentName, effectiveResourceGroupName))

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: effectiveResourceGroupName
  location: location
  tags: tags
}

module stack 'resources.bicep' = {
  name: 'acas-toolkit-stack'
  scope: rg
  params: {
    location: location
    foundryLocation: foundryLocation
    resourceToken: resourceToken
    tags: tags
    enableObservability: enableObservability
    enableFoundry: enableFoundry
    foundryModelName: foundryModelName
    foundryModelVersion: foundryModelVersion
    foundryModelSkuName: foundryModelSkuName
    foundryModelCapacity: foundryModelCapacity
  }
}

// -- Outputs (consumed by azd or `az deployment sub create`) ------------------

output ACAS_LOCATION string = location
output ACAS_RESOURCE_GROUP string = rg.name
output ACAS_SUBSCRIPTION_ID string = subscription().subscriptionId
output ACAS_SANDBOX_GROUP string = stack.outputs.sandboxGroupName
output ACAS_STORAGE_ACCOUNT string = stack.outputs.storageAccountName
output ACAS_IDENTITY_CLIENT_ID string = stack.outputs.identityClientId
output APPLICATIONINSIGHTS_CONNECTION_STRING string = stack.outputs.appInsightsConnectionString
output AZURE_AI_PROJECT_ENDPOINT string = stack.outputs.foundryProjectEndpoint
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = stack.outputs.foundryModelDeploymentName
