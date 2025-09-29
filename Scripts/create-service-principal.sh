#!/bin/bash

# -------------------------
# VARIABLES - customize these
# -------------------------
SP_NAME="CICD-SP"
RESOURCE_GROUP="LiotRAG"
SUBSCRIPTION_ID=""

# -------------------------
# 1️⃣ Create the Service Principal
# -------------------------
echo "Creating Service Principal: $SP_NAME ..."
SP_OUTPUT=$(az ad sp create-for-rbac \
    --name "$SP_NAME" \
    --role Contributor \
    --scopes "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" \
    --sdk-auth \
    --output json)

echo "Service Principal created successfully!"
echo "$SP_OUTPUT"

# Extract the service principal objectId for role assignments
SP_APP_ID=$(echo $SP_OUTPUT | jq -r '.clientId')

# -------------------------
# 2️⃣ Assign additional roles
# -------------------------
echo "Assigning 'Storage Blob Data Contributor' role at subscription level..."
az role assignment create \
    --assignee "$SP_APP_ID" \
    --role "Storage Blob Data Contributor" \
    --scope "/subscriptions/$SUBSCRIPTION_ID"

echo "Assigning 'Website Contributor' role at subscription level..."
az role assignment create \
    --assignee "$SP_APP_ID" \
    --role "Website Contributor" \
    --scope "/subscriptions/$SUBSCRIPTION_ID"

echo "All role assignments completed."
