// Microsoft Foundry — AI Services account + project + model deployment.
//
// Modern Foundry uses a multi-tenant ``Microsoft.CognitiveServices``
// account (``kind: 'AIServices'``) with a project sub-resource. The
// account hosts the model deployments; the project is the agent
// namespace that the harness's ``FoundryChatClient`` targets via
// ``project_endpoint``.
//
// Endpoint shape:
//   account:  https://${name}.services.ai.azure.com
//   project:  https://${name}.services.ai.azure.com/api/projects/${projectName}
//
// AAD-only auth: ``disableLocalAuth: true`` — the harness UAMI gets
// ``Cognitive Services User`` in rbac.bicep.

param location string
param tags object
param accountName string
param projectName string
param modelName string
param modelSkuName string
param modelCapacity int

@description('Model format / provider. ``OpenAI`` covers all GPT and gpt-* family models on Foundry.')
param modelFormat string = 'OpenAI'

@description('Model version. Leave empty to let Foundry pick the latest.')
param modelVersion string = ''

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: accountName
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: 'ACAS Toolkit default Foundry project.'
    displayName: projectName
  }
}

// Model deployment lives at the account scope (not the project). All
// projects under the account share the deployment.
//
// The ``version`` field is optional; if empty Foundry picks the latest
// model version that ships with the family. We pass it through only
// when non-empty so callers can pin if they need to.
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: modelName
  sku: {
    name: modelSkuName
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: modelFormat
      name: modelName
      version: empty(modelVersion) ? null : modelVersion
    }
  }
  dependsOn: [
    project
  ]
}

output accountName string = account.name
output accountId string = account.id
output accountEndpoint string = 'https://${account.name}.services.ai.azure.com'
output projectName string = project.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output modelDeploymentName string = modelDeployment.name
