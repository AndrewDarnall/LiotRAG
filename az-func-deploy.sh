#!/bin/bash
set -e

# ---------- CONFIG ----------
FUNCTION_APP_NAME="${AZURE_FUNCTION_APP_NAME}"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP}"
STORAGE_ACCOUNT="${AZURE_STORAGE_ACCOUNT}"
CONTAINER_NAME="${AZURE_FUNCTION_APP_STORAGE_CONTAINER}"
ZIP_FILE="./function-app.zip"
SAS_EXPIRY_MINUTES=10  # SAS valid for 10 minutes
# ----------------------------

# 1️⃣ Zip the function app
echo "[1/4] Creating ZIP..."
cd ./src/function-app
zip -r "../../function-app.zip" . > /dev/null
cd ../../

# 2️⃣ Upload ZIP to blob storage (AD auth)
echo "[2/4] Uploading ZIP to blob storage..."
az storage blob upload \
    --account-name "$STORAGE_ACCOUNT" \
    --container-name "$CONTAINER_NAME" \
    --name "function-app.zip" \
    --file "$ZIP_FILE" \
    --overwrite \
    --auth-mode login

# 3️⃣ Generate SAS token (user delegation SAS)
echo "[3/4] Generating SAS token..."
# Generate UTC expiry using Python (portable on Alpine runners)
BLOB_EXPIRY=$(python3 -c "from datetime import datetime, timedelta; print((datetime.utcnow() + timedelta(minutes=$SAS_EXPIRY_MINUTES)).strftime('%Y-%m-%dT%H:%MZ'))")

BLOB_SAS=$(az storage blob generate-sas \
    --account-name "$STORAGE_ACCOUNT" \
    --container-name "$CONTAINER_NAME" \
    --name "function-app.zip" \
    --permissions r \
    --expiry "$BLOB_EXPIRY" \
    --https-only \
    --auth-mode login \
    --as-user \
    -o tsv)

# 4️⃣ Set Function App to run from package
BLOB_URL="https://$STORAGE_ACCOUNT.blob.core.windows.net/$CONTAINER_NAME/function-app.zip?$BLOB_SAS"

echo "[4/4] Configuring Function App to run from ZIP..."
az functionapp config appsettings set \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --settings "WEBSITE_RUN_FROM_PACKAGE=$BLOB_URL"

# 5️⃣ Verify
echo "✅ Deployment complete. Verifying..."
az functionapp config appsettings list \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query "[?name=='WEBSITE_RUN_FROM_PACKAGE']"
