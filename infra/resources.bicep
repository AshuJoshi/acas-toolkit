// =============================================================================
// ACAS Toolkit — resources.bicep (RG scope)
// =============================================================================

targetScope = 'resourceGroup'

param location string
@description('Region for the Foundry account + project + model deployment. May differ from `location` when the desired model is not in the primary region\'s OpenAI catalog.')
param foundryLocation string = location
param resourceToken string
param tags object
param enableObservability bool
param enableFoundry bool
param foundryModelName string
@description('Foundry model version. Empty string means "let Foundry pick the default" (rejected for gpt-5-* family).')
param foundryModelVersion string = ''
param foundryModelSkuName string
param foundryModelCapacity int

// --- Naming ---
var sandboxGroupName    = 'sg-acas-${resourceToken}'
var storageAccountName  = take('stacas${resourceToken}', 24)
var identityName        = 'id-acas-${resourceToken}'
var logAnalyticsName    = 'log-acas-${resourceToken}'
var appInsightsName     = 'appi-acas-${resourceToken}'
var foundryAccountName  = 'aif-acas-${resourceToken}'
var foundryProjectName  = 'proj-acas'

// --- Modules ---
module sandboxGroup 'modules/sandboxgroup.bicep' = {
  name: 'sandboxGroup'
  params: {
    location: location
    tags: tags
    sandboxGroupName: sandboxGroupName
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    tags: tags
    accountName: storageAccountName
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    location: location
    tags: tags
    identityName: identityName
  }
}

module monitoring 'modules/monitoring.bicep' = if (enableObservability) {
  name: 'monitoring'
  params: {
    location: location
    tags: tags
    logAnalyticsName: logAnalyticsName
    appInsightsName: appInsightsName
  }
}

module foundry 'modules/foundry.bicep' = if (enableFoundry) {
  name: 'foundry'
  params: {
    location: foundryLocation
    tags: tags
    accountName: foundryAccountName
    projectName: foundryProjectName
    modelName: foundryModelName
    modelVersion: foundryModelVersion
    modelSkuName: foundryModelSkuName
    modelCapacity: foundryModelCapacity
  }
}

// --- Outputs ---
output sandboxGroupName string = sandboxGroup.outputs.name
output storageAccountName string = storage.outputs.accountName
output identityClientId string = identity.outputs.clientId
output appInsightsConnectionString string = enableObservability ? monitoring.outputs.appInsightsConnectionString : ''
output foundryProjectEndpoint string = enableFoundry ? foundry.outputs.projectEndpoint : ''
output foundryModelDeploymentName string = enableFoundry ? foundry.outputs.modelDeploymentName : ''
