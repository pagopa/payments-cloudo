#!/bin/bash

end=$((SECONDS + 5))

export RESOURCE_NAME="minikube"
export RESOURCE_RG="my-rg"
export AKS_NAMESPACE="default"


echo $RESOURCE_NAME $RESOURCE_RG
printenv | grep -E "AKS|MONITOR|CLOUDO"
while [ $SECONDS -lt $end ]; do
    # Print environment variables containing AKS or MONITOR
    echo "Test message at second: $SECONDS"
    sleep 5
done

exit 0
