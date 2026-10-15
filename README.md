# AngleCircle — czterowarstwowa płytka PCB do lokalizacji dźwięku

**AngleCircle** to projekt **okrągłej, czterowarstwowej płytki PCB**, opracowywanej w **KiCadzie** do systemu lokalizacji kierunku źródła dźwięku. System wykorzystuje **ESP32-S3** i **cztery cyfrowe mikrofony MEMS I²S**, rozmieszczone wokół płytki.

Celem projektu jest integracja elektroniki, zasilania i czujników w jednym module oraz ograniczenie problemów z połączeniami przewodowymi, które występowały na etapie prototypowania.

## Założenia projektowe

- Okrągła geometria płytki dostosowana do układu czterech mikrofonów.
- **Cztery warstwy miedzi** umożliwiające rozdzielenie sygnałów, zasilania oraz pól masy.
- Projektowanie ciągłych ścieżek prądu powrotnego i odpowiedniego połączenia obszarów GND za pomocą przelotek.
- Prowadzenie linii **I²S** (BCLK, WS/LRCLK i DATA) z uwzględnieniem integralności sygnałów i geometrii połączeń.
- Integracja z mikrokontrolerem **ESP32-S3** oraz elementami elektroniki pomocniczej.
- Przygotowanie schematu i layoutu PCB w **KiCadzie**.

## Architektura mikrofonów

Cztery mikrofony tworzą dwie pary pomiarowe, używane do estymacji opóźnienia nadejścia dźwięku (**TDOA**):

```text
         GÓRA

  MIC 1          MIC 2
 (lewy górny)  (prawy górny)
       \        /
        \      /
         \    /
          \  /
           \/
           /\
          /  \
         /    \
        /      \
       /        \
  MIC 3          MIC 4
 (lewy dolny) (prawy dolny)

         DÓŁ

 I²S1: MIC 1 + MIC 4
 I²S0: MIC 2 + MIC 3
```

Przyjęta w firmware koncepcja pomiaru łączy mikrofony w **dwie przekątne**. Każda przekątna jest obsługiwana przez oddzielny kontroler I²S, aby zapewnić synchronizację wewnątrz pary pomiarowej.
