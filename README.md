# LiteY

軽量な掲示板です

## デバッグの開始

データベースを起動します。

```bash
docker run --detach \
  --name litey-mongo-debug \
  --volume litey-mongo-debug:/data/db \
  --publish 27017:27017 \
  mongo
```

サーバーを起動します

```bash
fastapi dev
```

## デプロイ

今すぐデプロイ！

- https://litey.trade/
