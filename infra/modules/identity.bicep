// User-Assigned Managed Identity for consumer applications.
//
// One UAMI for ACR pull + Cosmos data plane + Storage blob data +
// Foundry data plane + sandbox group control plane. Putting them all
// on a single identity is convenient when one application is the only
// thing running in the Container App or similar host; split as needed
// for tighter RBAC scoping.

param location string
param tags object
param identityName string

resource id 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

output id string = id.id
output name string = id.name
output principalId string = id.properties.principalId
output clientId string = id.properties.clientId
