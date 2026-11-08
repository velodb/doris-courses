<a id="unit-u.5a108b192f1f4ff98a51f21893422259"></a>

<a id="optional-reading-continuous-ingestion"></a>

# Қосымша оқу: үздіксіз жүктеу

| Материал туралы ақпарат | Мәні |
| --- | --- |
| Курс | Apache Doris көмегімен деректер қоймасын құру · 1-деңгей · 5-модуль |
| Doris | Apache Doris 4.1.3 |
| Зертханалық жұмыстар | Қосымша [5A жұмысы](optional05_kafka_routine_load.ipynb) (Kafka, Docker жады 8 GiB) және [5B жұмысы](optional05_flink_mysql_cdc.ipynb) (MySQL және Flink CDC, 10 GiB) |
| Болжалды уақыт | Шамамен 40 минут: оқу 30, тест 10; жұмыстарға қосымша 30 және 40 минут кетеді |

[5-модуль сабағы](course.md) · [Тестті ашу](quiz05_continuous_ingestion.ipynb)
<a id="unit-u.d0408b5ad0b44734b7e7cdca9ae82bde"></a>

<a id="goal"></a>

## Мақсаты

Doris хабарлар ағынынан не дерекқор өзгерістерінен тоқтаусыз келетін деректерді қалай жүктей беретінін және мұндай тапсырма деректерді жоғалтпай не қайталамай қалай жалғасатынын түсініңіз.

Бұл материал таңдау бойынша: 6 және 7-модульдер мен негізгі тест оған тәуелді емес. Оның тесті төмендегі мақсаттарды тексереді, ал машинаңыздың жады жеткілікті болса, 5A және 5B жұмыстары екі жолды орындауға мүмкіндік береді.
<a id="unit-u.0e84234bf38f47cbb5031319eb06c555"></a>

<a id="learning-objectives"></a>

## Оқу мақсаттары

Осы материалды аяқтағаннан кейін сіз:

1. Үздіксіз жүктеу тапсырмасы неге өз барысын сақтайтынын және оның бизнес нұсқасынан айырмасын түсіндіресіз.
2. Routine Load тапсырмасының күйі мен барысын оқып, кідірту, жалғастыру және тоқтатудың бірін таңдайсыз.
3. CDC конвейері snapshot-тан инкременттік өзгерістерге қалай өтетінін және checkpoint не savepoint нені сақтайтынын түсіндіресіз.
4. Жаңа үздіксіз тапсырма оқуды қайдан бастайтынын таңдайсыз: басынан, қазіргі сәттен не сақталған орыннан.
5. Бастапқы дерекқордағы жою Doris Unique Key кестесіне қалай жететінін түсіндіресіз.
6. Үздіксіз файл тапсырмасы жаңа файлдарды қалай табатынын және атауы ескі ретпен келген файлды неге өткізіп жіберетінін түсіндіресіз.
<a id="unit-u.056e1f4cce8b43529fcf2c56de1236df"></a>

<a id="structure"></a>

## Құрылымы

| Бөлім | Уақыт | Нәтиже |
| --- | ---: | --- |
| Барыс тапсырмаға тиесілі | 4 мин | Ығысулар, орындар және олардың нұсқа еместігі |
| Kafka және Routine Load | 8 мин | Тақырыпты тұтынатын тапсырма және оны орындау |
| Flink CDC және Doris коннекторы | 7 мин | Snapshot, өзгерістер, checkpoint-тар және жоюлар |
| Жаңа тапсырма қайдан басталады | 4 мин | Басынан, қазіргі сәттен не сақталған орыннан |
| Streaming Jobs және CDC_STREAM | 4 мин | Doris өзі орындайтын сол жол |
| Объектілік сақтаудағы жаңа файлдар | 3 мин | Тапсырма жаңа файлдарды қалай табады |
| Тест | 10 мин | Осы мақсаттарға арналған алты сұрақ |
<a id="unit-u.28230e7fe8bd403493932056117c2116"></a>

<a id="progress-belongs-to-the-job"></a>

## Барыс тапсырмаға тиесілі

Файл бір рет жүктеледі; ағын бітпейді. Ағынды жүктейтін тапсырма қаншасын жүктегенін есте сақтауы керек, сонда кідірістен, қайта іске қосудан не сәтсіздіктен кейін сол жерден жалғастырады: бос аралықсыз және екі рет жүктеусіз. Сол орын — тапсырманың **барысы**.

Kafka-да **topic** — **бөлімдерге** бөлінген хабарлар ағыны; әр хабардың 0-ден саналатын бөлім ішіндегі нөмірі — **offset** (ығысуы) бар. Routine Load тапсырмасының барысы — әр бөлімде соңғы жүктелген offset. Әртүрлі бөлімдердің offset-тері оқиғаларды өзара реттемейді. CDC тапсырмасының барысы — дерекқор өзгерістер журналындағы орын, мысалы MySQL binlog файлы мен орны.

Барыс «бұл тапсырма қай жерге дейін оқыды?» дегенге жауап береді. Ол «бұл тапсырыстың қай күйі жаңарақ?» дегенге жауап бермейді: бұл — 7-модульдегі `event_version` сияқты бизнес нұсқасы. Жеткізу растауы кешіккен төлем оқиғасынан бұрын оқылуы мүмкін: барыс алға жылжиды, ал тапсырыс күйі артқа қайтпауы керек. Екеуін де сақтаңыз.
<a id="unit-u.c20ea9321dfa42708811b96f9546e3e2"></a>

<a id="kafka-and-routine-load"></a>

## Kafka және Routine Load

**Routine Load** тапсырмасы Doris ішінде тұрады: Kafka topic-ін оқып, оқығанын шағын пакеттермен, әрқайсысын бір жүктеу транзакциясы ретінде кестеге жазады және әр бекітуде барысын тіркейді. Ол барысын қарапайым Kafka consumer group бекіткен offset ретінде емес, Doris ішінде сақтайды.

```text
Producers ──> Kafka topic (partitions, offsets)
                  │ the job reads continuously
                  ▼
           Routine Load job: a load task every few seconds, progress saved with each commit
                  │
                  ▼
            Doris table
```

Сызбадағы жазулар: Producers — өндірушілер; Kafka topic (partitions, offsets) — Kafka тақырыбы (бөлімдер, ығысулар); the job reads continuously — тапсырма үздіксіз оқиды; Routine Load job: a load task every few seconds, progress saved with each commit — Routine Load тапсырмасы: бірнеше секунд сайын жүктеу жұмысы, барыс әр бекітуде сақталады; Doris table — Doris кестесі.

`SHOW ROUTINE LOAD FOR <job>` тапсырманы көрсетеді: `State` (RUNNING, PAUSED, STOPPED немесе жұмыстар арасындағы NEED_SCHEDULE), `Progress` (әр бөлімнің соңғы жүктелген offset-і), `Lag` (әр бөлімдегі күтіп тұрған хабарлар саны), `Statistic` (жүктелген және қабылданбаған жолдар), ал өздігінен кідірсе, `ReasonOfStateChanged` және `ErrorLogUrls`. Кідірту, жалғастыру және тоқтату Doris-ке емес, тапсырмаға әсер етеді:

| Оператор | Не болады | Барыс |
| --- | --- | --- |
| `PAUSE ROUTINE LOAD FOR <job>` | Тапсырма оқуды тоқтатады; хабарлар Kafka-да күтеді | Сақталады |
| `RESUME ROUTINE LOAD FOR <job>` | Өз барысынан қайта оқиды | Сақталады |
| `STOP ROUTINE LOAD FOR <job>` | Тапсырма біржола аяқталады, жалғастыру мүмкін емес | Тапсырмамен бірге жоғалады |

Қайта бастау үшін тапсырманы жойып, қайта жасамаңыз: жаңа тапсырма бапталған offset-терден қайта басталып, ескі хабарларды екінші рет жүктеуі мүмкін.

Тапсырма өзін де кідірте алады. `max_error_number` = 0 болса, кестеге сәйкес келмейтін алғашқы жол оны кідіртеді. 5A жұмысы мұның нені кері қайтаратынын және нені кері қайтармайтынын көрсетеді: сол жұмыстың дұрыс жолдары жүктеледі, ал барыс қате хабардан өтіп кеткен болады, сондықтан `RESUME` одан кейін жалғасады және түзетілген хабарды қайта жіберу керек. Бұл `max_filter_ratio` = 0 бар, ештеңе жүктемейтін Stream Load пакетінен өзгеше.

**Сыртқы орта мысалы; зертханалық жұмыста орындалмайды.** Арнайы topic жасап, тақырып жолы жоқ екі CSV хабарын жіберіңіз: `901001,180.00` және `901002,80.00`. Тапсырма олардың бірінші және екінші мәнін тапсырыс ID-і мен сомасына сәйкестендіреді:

<!-- external-service-example -->
```sql
CREATE TABLE orders_kafka_demo (
    order_id BIGINT, order_amount DECIMAL(18,2)
) DUPLICATE KEY(order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS 1
PROPERTIES("replication_num"="1");

CREATE ROUTINE LOAD orders_kafka_job ON orders_kafka_demo
COLUMNS TERMINATED BY ",",
COLUMNS(order_id, order_amount)
PROPERTIES("strict_mode"="true", "max_error_number"="0")
FROM KAFKA(
    "kafka_broker_list"="<broker>:9092",
    "kafka_topic"="<dedicated_topic>",
    "property.kafka_default_offsets"="OFFSET_BEGINNING"
);
SHOW ROUTINE LOAD FOR orders_kafka_job;
SELECT COUNT(*) AS orders, SUM(order_amount) AS amount FROM orders_kafka_demo;
```

Екі хабар жүктелгенде 2 жол және 260.00, бір бөлімді topic үшін `Progress` `{"0":"1"}` күтіледі. Тапсырма жаңа хабарларды күтуді жалғастырады; topic босаса, аяқталмайды. [Routine Load](https://doris.apache.org/docs/4.x/data-operate/import/import-way/routine-load-manual/) бөлімін қараңыз.
<a id="unit-u.96d0478a3bb74308a48432b87b08dbf1"></a>

<a id="flink-cdc-and-the-doris-connector"></a>

## Flink CDC және Doris коннекторы

**CDC**, өзгерістерді тіркеу, бизнес дерекқорының бекітілген енгізулерін, жаңартуларын және жоюларын MySQL binlog сияқты өзгерістер журналынан оқиды. MySQL CDC коннекторы бар Flink тапсырмасы кестені оқиды, Doris коннекторы нәтижені Doris-ке жазады:

```text
MySQL: the table's rows, then its binlog
        │
        ▼
Flink job: MySQL CDC source ── processing ── Doris sink
        │ checkpoints: binlog position and operator state
        ▼
Doris Unique Key table
```

Сызбадағы жол: MySQL кестесінің жолдары, содан кейін binlog → Flink тапсырмасындағы MySQL CDC дереккөзі → өңдеу → Doris қабылдағышы → Doris Unique Key кестесі. Checkpoint-тар binlog орнын және оператор күйін сақтайды.

Жаңа тапсырма әдетте алдымен бар жолдарды, яғни **snapshot** оқып, кейінгі әр өзгеріс үшін binlog-қа өтеді. Орындалу барысында тапсырма тұрақты аралықпен **checkpoint** сақтайды: binlog орнын және операторларының күйін. Екі фазалы бекітуде Doris қабылдағышы әр checkpoint жолдарын checkpoint аяқталғанда Doris транзакциясы ретінде бекітеді, сондықтан checkpoint-тен қайта бастау жолдарды жоғалтпайды да, қайталамайды да. **Savepoint** — өзіңіз сұрайтын checkpoint, мысалы тапсырманы тоқтатып, кейін дәл сол орыннан қалпына келтіру үшін `flink stop` арқылы. Екеуіне де binlog ішінде сол орыннан бергі әр өзгеріс әлі сақталуы қажет: тазаланған binlog-ты қайта ойнату мүмкін емес.

MySQL жаңартуы Doris ішінде сол кілттің жаңа күйіне айналады, Unique Key кестесі оны бір рет сақтайды. Жою жою ретінде келуі керек: `sink.enable-delete` қосылғанда Doris коннекторы оны жоюға белгіленген жол ретінде жібереді, Unique Key кестесі кілтті алып тастайды. 7-модуль жүктеудегі сол жою белгісін және оның нұсқаларға әсерін көрсетеді. Бастапқы кілтті, мақсатты Unique Key-ді және коннектор параметрлерін бірге тексеріп, конвейерге сенбес бұрын енгізу, жаңарту, жою және қайта іске қосуды сынаңыз. [Flink Doris Connector](https://doris.apache.org/docs/4.x/connection-integration/data-integration/flink-doris-connector/) бөлімін қараңыз.

MySQL ішіндегі `SHOW MASTER STATUS` binlog соңын, яғни MySQL қаншаға дейін жазғанын көрсетеді; бұл ешбір оқырманның жеткен орны емес. 5B жұмысы осы толық жолды орындайды: snapshot, өзгерістер, дереккөз өзгеріп жатқандағы savepoint және қуып жететін қалпына келтіру.
<a id="unit-u.d1b1e183b30348eda6bcf2a0b397ef18"></a>

<a id="where-a-new-job-starts"></a>

## Жаңа тапсырма қайдан басталады?

Жалғасатын тапсырма сақталған орнынан жалғастырады: Routine Load өз барысынан, Flink тапсырмасы checkpoint не savepoint-тен. Жаңа тапсырмаға бастапқы орын қажет, таңдау оның не жүктейтінін анықтайды:

| Бастау | Routine Load | Flink ішіндегі MySQL CDC | Мақсатты кесте не алады |
| --- | --- | --- | --- |
| Басынан | `kafka_default_offsets` = `OFFSET_BEGINNING` | `scan.startup.mode` = `initial`: snapshot, содан кейін binlog | Бар деректердің бәрі, содан кейін әр өзгеріс |
| Қазіргі сәттен | `OFFSET_END` | `latest-offset` | Тек бастағаннан кейін келгені: бұрынғы деректер жоқ |
| Сақталған орыннан | Тапсырманың өз барысы | Checkpoint не savepoint немесе binlog файлы мен орны бар `specific-offset` | Дәл сол орыннан кейінгі деректер |

CDC коннекторының `timestamp` режимі сияқты уақыттан бастау сол уақытта журналға жазылған алғашқы өзгерісті табады, бірақ сол секундтың қай өзгерістері жүктелгенін айтпайды: оны жалғастыруға емес, жаңа тапсырма бастауға қолданыңыз. Бір тапсырма басынан толтырған кестені екінші тапсырма да басынан толтырмауы керек, әйтпесе Duplicate Key кестесі бәрін екі рет сақтайды.
<a id="unit-u.4bb653f9bf7947daba267fcb85fab0aa"></a>

<a id="streaming-jobs-and-cdc_stream"></a>

## Streaming Jobs және CDC_STREAM

**Нұсқа және қамту аясы:** бұл бөлім Doris 4.1-ден бастап қолжетімді үздіксіз синхрондауды таныстырады; ресми құжаттама оны MySQL мен PostgreSQL үшін Experimental деп белгілейді. Мысал курста орындалмайды; орындамас бұрын өз Doris нұсқаңыз үшін бастапқы дерекқорды, JDBC драйверін және синхрондау тіркелгісін дайындаңыз. [MySQL бір кестесін синхрондау](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-mysql-table/) бөлімін қараңыз.

Жол алдыңғы бөлімдердегідей: snapshot, өзгерістер және барыс, бірақ оны Doris орындайды: `CREATE JOB ... ON STREAMING` арқылы жасалған **Streaming Job** `INSERT INTO ... SELECT` операторын үздіксіз орындайды, ал `CDC_STREAM` кестелік функциясы дереккөз жолдары мен өзгерістерін оқиды:

```text
MySQL rows and binlog ──> CDC_STREAM ──> SELECT maps the columns ──> Doris Unique Key table
                     └──── Streaming Job runs it continuously and records its progress ────┘
```

Сызбадағы жол: MySQL жолдары мен binlog → CDC_STREAM → SELECT бағандарды сәйкестендіреді → Doris Unique Key кестесі; Streaming Job мұны үздіксіз орындап, барысын тіркейді.

`"offset"="initial"` алдымен бар жолдарды, содан кейін өзгерістерді оқиды; `"offset"="latest"` тек кейінгі өзгерістерді оқиды. Кестелер тобын бастапқы құрылымымен қадағалау үшін мақсатты кестелерді жасайтын `FROM MYSQL (...) TO DATABASE ...` мүмкіндігін бағалаңыз; кесте жасау және кейінгі схема өзгерістерін қадағалау — бөлек мәселелер. [CDC_STREAM](https://doris.apache.org/docs/4.x/sql-manual/sql-functions/table-valued-functions/cdc-stream/) және [MySQL бүкіл дерекқорын синхрондау](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-mysql-database/) бөлімдерін қараңыз.

**Сыртқы орта мысалы; зертханалық жұмыста орындалмайды.** Бастапқы `demo.orders` кестесінде `order_id` бастапқы кілті және `status` бар, бастапқыда бір `(901001, 'CREATED')` жолы бар:

<!-- external-service-example -->
```sql
CREATE TABLE orders_cdc_demo (
    order_id BIGINT NOT NULL, status VARCHAR(20)
) UNIQUE KEY(order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS 1
PROPERTIES("replication_num"="1");

CREATE JOB orders_mysql_job ON STREAMING DO
INSERT INTO orders_cdc_demo (order_id, status)
SELECT order_id, status FROM CDC_STREAM(
    "type"="mysql", "jdbc_url"="jdbc:mysql://<mysql_host>:3306",
    "driver_url"="<driver_jar_url>", "driver_class"="com.mysql.cj.jdbc.Driver",
    "user"="<sync_user>", "password"="<sync_password>",
    "database"="demo", "table"="orders", "offset"="initial"
);
SELECT * FROM jobs("type"="insert")
WHERE ExecuteType = 'STREAMING' AND Name = 'orders_mysql_job';
SELECT order_id, status FROM orders_cdc_demo ORDER BY order_id;
```

Алдымен жол CREATED күйінде келеді. MySQL-де оны PAID еткеннен кейін сұрау енді PAID күйіндегі бір жол көрсетуі керек: Unique Key әр тапсырысқа бір жол сақтайды. Тапсырма күйі мен барысын `jobs()` ішінен, бизнес нәтижесін кестеден оқыңыз. Тапсырманы `PAUSE JOB WHERE jobName = 'orders_mysql_job'` арқылы кідіртіп, `RESUME JOB WHERE jobName = 'orders_mysql_job'` арқылы жалғастырыңыз. [Үздіксіз жүктеу тапсырмаларын басқару](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-overview/) бөлімін қараңыз.
<a id="unit-u.0a316f1955794326a164f5f42c3705ae"></a>

<a id="new-files-in-object-storage"></a>

## Объектілік сақтаудағы жаңа файлдар

Бекітілген файлдар жиыны бір рет жүктеледі (5-модуль); жаңа файлдар келе беретін каталогқа оларды байқайтын тапсырма қажет. `S3` кестелік функциясының үстіндегі Streaming Job мұны атқарып, жаңа файлдарды атау бойынша табады: атауы соңғы жүктеген файлдан кейін сұрыпталса, файл жаңа болып саналады.

| Файл келеді | Тапсырма не істейді |
| --- | --- |
| `orders-001.parquet` 09:00 кезінде | Жүктеп, өз барысы ретінде тіркейді |
| `orders-002.parquet` 09:05 кезінде | Атауы барыстан кейін сұрыпталады: жүктейді |
| `orders-000.parquet` 09:10 кезінде | Атауы барыстан бұрын сұрыпталады: өткізіп жібереді; бөлек жүктеңіз |

Сондықтан файлдарды тұрақты ені бар пакет нөмірлері сияқты өсе беретін атаулармен, кешіккен деректер үшін де жариялаңыз: бүгін келген кешегі тапсырыс бүгінгі файлға түседі. Ескі атаудағы файлды қайта жазу жаңа пакет емес. Тапсырманың `CurrentOffset` өрісі соңғы өңделген файлды, `EndOffset` ағымдағы пакеттің соңын атайды; файл келіп, жолдары келмесе, алдымен оның атауын осылармен салыстырыңыз. [Объектілік сақтаудан үздіксіз жүктеу](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-s3/) бөлімін қараңыз.

**Сыртқы орта мысалы; зертханалық жұмыста орындалмайды.** Routine Load мысалының екі бағандық кестесін және тек жаңа файлдар бар каталогты қайта пайдаланыңыз:

<!-- external-service-example -->
```sql
CREATE TABLE orders_files_demo LIKE orders_kafka_demo;
CREATE JOB orders_files_job ON STREAMING DO
INSERT INTO orders_files_demo (order_id, order_amount)
SELECT order_id, order_amount FROM S3(
    "uri"="s3://<bucket>/incremental/orders-*.parquet",
    "s3.endpoint"="<endpoint>", "s3.region"="<region>",
    "s3.access_key"="<access_key>", "s3.secret_key"="<secret_key>",
    "format"="parquet"
);
SELECT * FROM jobs("type"="insert")
WHERE ExecuteType = 'STREAMING' AND Name = 'orders_files_job';
SELECT order_id, order_amount FROM orders_files_demo ORDER BY order_id;
```

`(901001, 180.00)` бар `orders-001.parquet` жариялап, жолды күтіңіз, содан кейін `(901002, 80.00)` бар `orders-002.parquet` жариялаңыз: өзіңіз еш `INSERT` орындамай, екі жол және 260.00 күтіңіз. Аяқтағанда файлдарды қабылдауды тоқтату үшін тапсырманы `PAUSE JOB WHERE jobName = 'orders_files_job'` арқылы кідіртіңіз.
<a id="unit-u.64678a054e884fe4b4752c73118db12c"></a>

<a id="summary"></a>

## Қорытынды

- Үздіксіз тапсырма өз барысын, Kafka offset-терін не дерекқор binlog орнын сақтайды, сондықтан бос аралықсыз не қайталаусыз жалғасады; қай күй жаңарақ екенін бизнес нұсқалары шешеді, бұл бөлек мәселе.
- Routine Load тапсырмасының `State`, `Progress`, `Lag` және `Statistic` өрістерін оқыңыз; барысын сақтау үшін кідіртіп, жалғастырыңыз, тек аяқтау үшін тоқтатыңыз; қате жолдан кідірген тапсырма сол жолдан өтіп кеткен болады.
- CDC тапсырмасы snapshot-ты, содан кейін өзгерістер журналын оқиды; checkpoint не savepoint оның орнын және оператор күйін сақтайды, ал қалпына келтіру үшін журналда содан бергі барлық өзгеріс әлі болуы керек.
- Жаңа тапсырма басынан, қазіргі сәттен не сақталған орыннан басталады; қазіргі сәттен бастау бұрынғыны тыс қалдырады, уақыт сақталған орынның орнына жүрмейді.
- Дереккөздегі жою Doris-ке тек жою ретінде жетеді: коннектор жолды жоюға белгілейді, Unique Key кестесі кілтті алып тастайды.
- Үздіксіз файл тапсырмасы атауы өз барысынан кейін сұрыпталатын файлдарды жүктейді; ертерек атаулы файл өткізіліп жіберіледі және бөлек жүктеуді қажет етеді.
<a id="unit-u.680d8e39a02c41cbb6e9c27515af6088"></a>

<a id="quiz"></a>

## Тест

[Үздіксіз жүктеу тестін](quiz05_continuous_ingestion.ipynb) ашыңыз. Онда бір дұрыс жауабы бар алты сұрақ бар және іске қосылған орта қажет емес.

[5-модуль сабағына](course.md) оралыңыз немесе [6-модуль: деректер сапасы және схеманы тексеру](../module06-data-quality/course.md) бөліміне өтіңіз.
<a id="unit-u.c7ae0bdfa8cd4ebabb92095ad470f4fb"></a>

<a id="references"></a>

## Дереккөздер

- [Apache Doris: Routine Load](https://doris.apache.org/docs/4.x/data-operate/import/import-way/routine-load-manual/)
- [Apache Doris: Flink Doris Connector](https://doris.apache.org/docs/4.x/connection-integration/data-integration/flink-doris-connector/)
- [Flink CDC 3.4: MySQL коннекторы](https://nightlies.apache.org/flink/flink-cdc-docs-release-3.4/docs/connectors/flink-sources/mysql-cdc/)
- [Apache Doris: Үздіксіз жүктеу шолуы](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-overview/)
- [Apache Doris: CREATE STREAMING JOB](https://doris.apache.org/docs/4.x/sql-manual/sql-statements/job/CREATE-STREAMING-JOB/)
- [Apache Doris: CDC_STREAM кестелік функциясы](https://doris.apache.org/docs/4.x/sql-manual/sql-functions/table-valued-functions/cdc-stream/)
- [Apache Doris: Объектілік сақтаудан үздіксіз жүктеу](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-s3/)
