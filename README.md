# LiteY

軽量な掲示板です

## デバッグの開始

依存関係をインストールします

```bash
pip install -r requirements.txt
```

データベースを起動します

```bash
docker run --detach \
  --name litey-mongo-debug \
  --volume litey-mongo-debug:/data/db \
  --publish 27017:27017 \
  mongo
```

```bash
docker run --detach \
  --name litey-redis-debug \
  --volume litey-redis-debug:/data/db \
  --publish 6379:6379 \
  redis
```

サーバーを起動します

```bash
fastapi dev
```

## デプロイ

今すぐデプロイ！

- https://litey.trade/
