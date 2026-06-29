# Prop Research

Исследовательская платформа для Monte Carlo-моделирования управления капиталом при прохождении проп-фирм.

## Что реализовано в v1

- конечный автомат жизненного цикла проп-счета;
- две challenge-фазы и funded-состояние через конфиг;
- сделки как последовательность `Win/Loss` без моделирования рынка;
- фиксированный риск на проп-счете;
- динамический риск на личном счете;
- зональная и непрерывная стратегии личного риска;
- денежная метрика `final wealth`;
- Monte Carlo-симулятор;
- простой grid search для fixed-risk baseline;
- минимальный Streamlit-интерфейс.

## Запуск тестов

```powershell
rtk python -m pytest -q
```

## Запуск интерфейса

```powershell
rtk python -m streamlit run prop_research/app/streamlit_app.py
```

## Streamlit Community Cloud

Main file path:

```text
prop_research/app/streamlit_app.py
```

## Главная логика

Если первая выплата получена, цикл успешен независимо от состояния личного счета.

Если проп-счет потерян до первой выплаты, провал считается recoverable только если:

```text
personal_balance >= challenge_fee
```
