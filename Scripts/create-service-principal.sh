#!/bin/bash
set -euo pipefail

# -------------------------
# VARIABLES - customize
# -------------------------
SP_NAME="CICD-SP"
RESOURCE_GROUP="LiotRAG"
ACR_NAME="liotragreg"
STORAGE_ACCOUNT="liotragblob"

# -------------------------
# 0️⃣ Ensure subscription ID is set
# -------------------------
if [ -z "${AZURE_SUBSCRIPTION_ID:-}" ]; then
    echo "AZURE_SUBSCRIPTION_ID not set. Detecting current subscription..."
    AZURE_SUBSCRIPTION_ID=$(az account show --query id -o tsv)
fi
echo "Using subscription ID: $AZURE_SUBSCRIPTION_ID"

# -------------------------
# 1️⃣ Delete existing SP if present
# -------------------------
EXISTING_APP_ID=$(az ad sp list --display-name "$SP_NAME" --query '[0].appId' -o tsv || echo "")

if [ -n "$EXISTING_APP_ID" ]; then
    echo "Deleting existing Service Principal: $SP_NAME (AppId: $EXISTING_APP_ID)..."
    az ad sp delete --id "$EXISTING_APP_ID"
fi

# -------------------------
# 2️⃣ Create new Service Principal without role assignments
# -------------------------
echo "Creating new Service Principal: $SP_NAME..."
SP_OUTPUT=$(az ad sp create-for-rbac \
    --name "$SP_NAME" \
    --skip-assignment \
    --sdk-auth \
    --output json)

# -------------------------
# 3️⃣ Assign roles manually (avoids CLI bug)
# -------------------------
# Extract the correct AppId
SP_APP_ID=$(echo $SP_OUTPUT | jq -r '.clientId')


# Contributor role on Resource Group
echo "Assigning 'Contributor' role on resource group $RESOURCE_GROUP..."

# Contributor role
az role assignment create \
  --assignee "$SP_APP_ID" \
  --role "Contributor" \
  --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"

# AcrPush role
ACR_SCOPE=$(az acr show --name "$ACR_NAME" --query id -o tsv)
az role assignment create \
  --assignee "$SP_APP_ID" \
  --role "AcrPush" \
  --scope "$ACR_SCOPE"

# Storage Blob Data Contributor
STORAGE_SCOPE=$(az storage account show --name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" --query id -o tsv)
az role assignment create \
  --assignee "$SP_APP_ID" \
  --role "Storage Blob Data Contributor" \
  --scope "$STORAGE_SCOPE"


echo "✅ Service Principal creation and role assignments complete."
echo "SDK-auth JSON credentials:"
echo "$SP_OUTPUT"
