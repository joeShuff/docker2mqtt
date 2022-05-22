# 3 Steps to building
Make sure docker desktop is running and you have tested the build locally.

1. `docker build --tag docker2mqtt .`
2. `docker image tag docker2mqtt denizenn\docker2mqtt`
3. `docker push denizenn\docker2mqtt`