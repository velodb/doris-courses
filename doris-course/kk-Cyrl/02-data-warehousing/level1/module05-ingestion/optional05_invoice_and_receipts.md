<a id="unit-u.43fa4d86bc7a42ef8b412194d3da76ac"></a>

<a id="further-reading-wwi-invoices-and-account-receipts-optional"></a>

# Қосымша оқу: WWI шот-фактуралары және шот түсімдері (таңдау бойынша)

[5-модульге оралу](course.md)

Алдымен 5-зертханалық жұмыста он тарихи кестені жүктеуді аяқтаңыз. Бұл бет тек бар кестелерді сұрайды, деректерді өзгертпейді;
ол әртүрлі сома анықтамаларын түсіндіреді және негізгі пакеттік жүктеу жолының міндетті қадамы емес.
<a id="unit-u.f998f18162aa499c80e2877492311621"></a>

<a id="do-not-mix-three-kinds-of-amounts"></a>

## Үш түрлі соманы араластырмаңыз

| Деректер | Нені білдіреді | Курста қолданылуы |
| --- | --- | --- |
| Тапсырыстар мен өнім жолдары | Тапсырыс берілген сан мен бірлік бағалары | Салыққа дейінгі тапсырыс сомасы үшін Quantity × UnitPrice қосу |
| Шот-фактуралар мен олардың жолдары | Шот-фактура жазбалары | Шот-фактура өрістерін алынған төлем деп қабылдамай түсіндіру |
| Тұтынушы шотының транзакциялары | Транзакция түрі бойынша тіркелген шот сомалары | Әр тапсырысқа күштеп бөлмей, бастапқы бухгалтерлік таңбаларды сақтау |
<a id="unit-u.fa8a4290968f48a39b20c3f2fe881513"></a>

<a id="inspect-account-records-by-transaction-type"></a>

## Шот жазбаларын транзакция түрі бойынша тексеру

```sql
SELECT t.TransactionTypeName, COUNT(*) AS rows_count,
       SUM(c.TransactionAmount) AS ledger_amount,
       SUM(CASE WHEN c.InvoiceID IS NULL THEN 1 ELSE 0 END) AS no_invoice_link
FROM wwi_customer_transactions c
JOIN wwi_transaction_types t ON c.TransactionTypeID=t.TransactionTypeID
GROUP BY t.TransactionTypeName ORDER BY t.TransactionTypeName;
```

Мұнда CASE байланысқан шот-фактура жоқ болса 1, әйтпесе 0 береді; SUM осы жазбаларды санайды.
Түсім түрлерінің таңбаларын және no_invoice_link мәнін бақылаңыз: осы үлгідегі теріс шот түсімдерін тікелей тапсырыс қайтарымы деп қабылдауға болмайды,
ал тапсырыс төлемімен байланыс болмағанда сомалар оларды күштеп бөлуді ақтамайды. 6-модуль CASE арқылы жіктеуді әрі қарай жаттықтырады.

```sql
SELECT SUM(TransactionAmount) AS ledger_balance,
       SUM(OutstandingBalance) AS outstanding_balance
FROM wwi_customer_transactions;
SELECT COUNT(*) AS delivered_invoices
FROM wwi_invoices WHERE ConfirmedDeliveryTime IS NOT NULL;
```

Курстың бекітілген WWI пакетінде бірінші сұраудың екі бағаны да 267011.44, ал екінші сұрау 70426 қайтарады.
Бұлар барлық компанияға тән бухгалтерлік теңдіктер емес, осы үлгінің тексеру мәндері; шот қалдықтары мен жеткізілген шот-фактура сандары тапсырыс төлем сомалары емес.
Модельденген жаңа тапсырыстардың төлемдері мен қайтарымдары 7-модульде осы WWI тарихынан бөлек бизнес транзакцияларын қолданады.

Курстың README файлындағы [Деректер жиыны](../../README.md#dataset) бөлімі дереккөзді және MIT лицензиясын көрсетеді; `datasets/wwi/manifest.json` кестелер мен бағандарын тізеді.
