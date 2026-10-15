# AngleCircle — czterowarstwowa płytka PCB do lokalizacji dźwięku

**AngleCircle** to projekt **okrągłej, czterowarstwowej płytki PCB**, opracowywanej w **KiCadzie** do systemu lokalizacji kierunku źródła dźwięku. System wykorzystuje **ESP32-S3** i **cztery cyfrowe mikrofony MEMS I²S**, rozmieszczone wokół płytki.

Celem projektu jest integracja elektroniki, zasilania i czujników w jednym module oraz ograniczenie problemów z połączeniami przewodowymi, które występowały na etapie prototypowania.

> **Status projektu:** PCB jest rozwijane i podlega weryfikacji projektowej. Działanie algorytmu lokalizacji zostało sprawdzone na wcześniejszym prototypie z czterema mikrofonami; nie oznacza to jeszcze, że gotowa płytka została wyprodukowana i przetestowana.

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

## Projektowanie PCB

W pracy nad płytką istotne są:

1. **Zasilanie 3,3 V** — prawidłowe doprowadzenie zasilania do układów i kondensatory odsprzęgające przy elementach.
2. **Pola masy GND** — możliwie ciągłe ścieżki powrotu prądu, w tym połączenia przelotkami pomiędzy obszarami masy, jeśli wymaga tego układ warstw.
3. **Magistrale I²S** — staranne prowadzenie linii zegarowych i danych, bez zbędnych odgałęzień oraz niepotrzebnego wydłużania tras.
4. **Interfejsy i montaż** — rozmieszczenie elementów, dostęp do połączeń oraz możliwość późniejszego montażu i diagnostyki.
5. **Weryfikacja projektu** — kontrola reguł DRC oraz sprawdzenie zgodności schematu z PCB przed produkcją.

Dokładne przypisanie GPIO i rozmieszczenie elementów należy zawsze sprawdzić w **aktualnej wersji plików KiCad**, ponieważ mogą się zmieniać podczas rozwijania płytki.

## Pliki projektu

Repozytorium zawiera m.in.:

- [`kolko_4layer.kicad_pro`](kolko_4layer.kicad_pro) — ustawienia projektu KiCad.
- [`PCB/kolko_4layer.kicad_pcb`](PCB/kolko_4layer.kicad_pcb) — projekt czterowarstwowej płytki.
- [`PCB/`](PCB/) — katalog związany z projektem PCB.

Otwórz pliki w **KiCadzie**. Przed eksportem Gerberów sprawdź aktualną konfigurację warstw, reguły projektowe oraz wyniki DRC.

## Powiązane oprogramowanie

Firmware obsługujący pomiary i lokalizację kąta znajduje się w osobnym repozytorium:

**[Source Localization System — ESP32-S3, C/C++, GCC-PHAT](https://github.com/Igur7/Source_Localization_System)**

Kod wyznacza kierunek nadejścia dźwięku na podstawie pomiarów z czterech mikrofonów i przesyła kąt przez port szeregowy. Repozytorium zawiera konfigurację PlatformIO oraz opis działania algorytmu.

Dodatkowy projekt: [Sound Classification System — analiza i klasyfikacja dźwięku w Pythonie](https://github.com/Igur7/sound-classification-system).

## Planowane kolejne kroki

- Zakończenie routingu i weryfikacja DRC.
- Przygotowanie plików produkcyjnych.
- Montaż elementów i uruchomienie PCB.
- Testy mikrofonów oraz dokładności lokalizacji w docelowym układzie.
