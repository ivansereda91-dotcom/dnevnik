# Дневники

Кинодневник и читательский дневник на своём сервере: одно приложение (PWA) для телефона и ноутбука, данные синхронизируются.

Установка на сервер:

```
git clone https://github.com/ivansereda91-dotcom/dnevnik /opt/dnevnik
bash /opt/dnevnik/setup.sh
```

Обновление: `cd /opt/dnevnik && git pull && systemctl restart dnevnik`

Данные лежат в `/var/lib/dnevnik/dnevnik.db` (в репозиторий не попадают). Копия — по ссылке «Скачать копию» на главной.
