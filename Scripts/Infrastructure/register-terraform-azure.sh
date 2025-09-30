#!/bin/bash


# Registers the Azure Terraform Service
az provider register --namespace Microsoft.AzureTerraform

# Checks the Registration Process
az provider show --namespace Microsoft.AzureTerraform --query "registrationState"