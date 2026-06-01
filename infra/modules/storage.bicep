// Storage account for the ACAS blob workspace volume.
//
// The ACAS volume itself is a data-plane resource on the sandbox
// group — not modeled in ARM today. We provision the storage account
// here for completeness (a) so a single ``azd up`` gives the user
// somewhere to land blob workspace data, and (b) so the UAMI can be
// granted ``Storage Blob Data Contributor`` on it ahead of time.
// Callers create the actual volume on the sandbox group on first
// use (see :func:`acas_toolkit.ensure_workspace_volume`).

param location string
param tags object
param accountName string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false   // AAD-only; harness UAMI uses RBAC
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

// A blob container so users can dump artifacts directly even outside
// the workspace-volume flow. Named ``workspace`` to mirror the volume
// name on the sandbox group.
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource workspaceContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'workspace'
  properties: {
    publicAccess: 'None'
  }
}

output accountName string = storage.name
output id string = storage.id
output blobEndpoint string = storage.properties.primaryEndpoints.blob
