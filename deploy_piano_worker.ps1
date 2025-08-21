# workers/piano_worker/deploy_piano_worker.ps1
$remoteServer  = "gabriel@195.201.9.184"
$imageName     = "gdefombelle/piano_worker:latest"
$containerName = "piano_worker"

Write-Host "🔨  Build…"
docker build --no-cache -t $imageName .

Write-Host "📦  Push…"
docker push $imageName

# ⚠️ monoligne
$remoteCommand = "docker network create pytune_network >/dev/null 2>&1 || true && docker stop $containerName || true && docker rm $containerName || true && docker image rm $imageName || true && docker pull $imageName && docker run -d --name $containerName --network pytune_network --env-file /home/gabriel/pytune.env -v /var/log/pytune:/var/log/pytune --restart always $imageName celery -A worker.piano_tasks:app worker --loglevel=info -Q i2i_tasks_queue"

ssh $remoteServer $remoteCommand

Write-Host "✅  Déployé: $containerName"
