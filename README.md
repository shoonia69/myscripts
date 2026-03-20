0. 
```
curl -fsSL https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/aio.sh | bash
```

1. подготовка вм: апдейт, установка утилит
```
curl -fsSL https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/prep_vpn/1-prepvm.sh | bash
```

2. установка докера
```
curl -fsSL https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/prep_vpn/2-dockerinstall.sh | bash
```

3. установка портейнера (по желанию)
```
curl -fsSL https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/prep_vpn/3-portainerinstall.sh | bash
```

4.1. установка mtproxy на порт 443 в режиме хост (443 порт должен быть свободен)
```
curl -fsSL https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/prep_vpn/4-1-mtproxy443.sh | bash
```

4.2. установка mtproxy на порт 10443 (переадресация в контейнер)
```
curl -fsSL https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/prep_vpn/4-2-mtproxy10443.sh | bash
```
