#!/bin/bash
echo "🚀 Deploying AgentHub..."

# Check env
if [ ! -f .env ]; then
    echo "❌ Error: .env file missing in infra/"
    exit 1
fi

echo "📦 Building containers..."
docker-compose -f docker-compose.yml build

echo "🔄 Restarting services..."
docker-compose -f docker-compose.yml up -d

echo "✅ Deployment complete! Access at http://localhost"
