# Máquinas de estado implícitas

## Alcance y método

**Definición.** Una máquina de estado implícita es una parte del código donde
una entidad pasa por un conjunto finito de estados a lo largo del tiempo. El
código no modela ese conjunto como un tipo cerrado con una única función de
transición. El estado vive en otra forma: un hash en un payload, la presencia
de un fichero, una fila en un JSONL, una combinación de campos opcionales, una
variable local de un bucle o el orden de los eventos de un stream.

**Método.** Primero leí las instrucciones de la raíz del repositorio,
`README.md`, `docs/DEPLOYMENT.md` y `docs/FIGURAS.md`. Después leí completo el código de `backend/src/rag/`,
`frontend/src/`, `tools/figuras/`, `frontend/nginx.conf`, `docker-compose.yml`,
`backend/Dockerfile`, `backend/eval/run_eval.py` y la cabecera de
`backend/e2e/run_e2e.py`. Busqué contadores, flags booleanos, campos `| None`,
ficheros de "hechos", marcadores de texto, bucles con variables de modo y
recursos que dos procesos leen y escriben. Comprobé cada referencia
`ruta:línea` contra el código. No ejecuté nada.

**Convención de riesgos.**

- *verificado*: el comportamiento se deduce de leer el código.
- *hipótesis*: el comportamiento depende de condiciones externas (Qdrant,
  Ollama, Docker, SLURM, red) o de datos que no vi.

Las secciones van de mayor a menor importancia.

## Resumen

| # | Máquina | Codificación del estado | Archivo principal | Riesgo principal |
|---|---|---|---|---|
| 1 | Ciclo de vida de un documento en el índice | `source_hash` del payload contra el `.md`, más flags de la corrida | `backend/src/rag/ingestion/indexer.py` | Documento vacío que queda en el índice con su versión vieja para siempre |
| 2 | Versión de un documento entre Khipu, Mac e índice | Contenido del `.md`, sello del frontmatter, listas `.txt` por oleada | `tools/figuras/aplicar_oleada.sh`, `fusionar_figuras.py` | El índice no sabe qué versión del `.md` tiene y los fallos de reindexado se ocultan |
| 3 | Bucle agéntico y resultado de la respuesta | `TypedDict` parcial: `scope`, `no_context`, `grounded`, `claims_total` | `backend/src/rag/graph/agent.py` | `no_context` sale con `grounded: true` e infla la métrica del eval |
| 4 | Veredicto por afirmación del verificador | Listas de `(estado, motivo)` por afirmación en dos rondas | `backend/src/rag/graph/agent.py` | Tope de 25 afirmaciones con insignia verde sobre el total |
| 5 | Protocolo SSE de `/api/chat` | Orden de eventos de LangGraph, flag `sources_sent`, dict `final` | `backend/src/rag/api/main.py` | La cuota se consume antes del stream; `final` duplica el estado del grafo |
| 6 | Mensaje del asistente en el frontend | Campos opcionales de `ChatMessage` | `frontend/src/App.tsx` | Una respuesta detenida se ve como completa y entra al historial |
| 7 | Cadena SLURM de lectura y verificación | Filas sin `error` en dos JSONL, sucesor en la cola | `tools/figuras/figuras_job.slurm` | Sin estado terminal: una página que falla siempre encadena jobs sin fin |
| 8 | Estado de una página en el pipeline de figuras | Presencia de `.jpg`, filas JSONL, cabecera en el `.md` | `tools/figuras/prep_figuras.py`, `extraer_figuras.py` | Rehacer el worklist pierde rescates y páginas ya sin marcador |
| 9 | Fusión en el vault por oleadas | El propio texto del `.md` (cabeceras y marcadores) | `tools/figuras/fusionar_figuras.py` | Bloques de formato viejo sin salida salvo el cierre |
| 10 | Procedencia de las lecturas en el chunker | Variables locales `activa` y `ultima` | `backend/src/rag/ingestion/chunker.py` | Bloque sin cierre marca el resto del documento como lectura de máquina |
| 11 | Esquema de puntos y colección en Qdrant | Presencia de `*_norm`, grafía de `tipo`, dimensión fija | `backend/src/rag/ingestion/indexer.py` | Cambio de embedder sin registro; backfill sin llamador |
| 12 | Cuota diaria por IP | Filas `(ip, day, count)` en SQLite | `backend/src/rag/api/quota.py` | Sana; el aviso del frontend queda viejo tras guardar la clave |
| 13 | Salud del servicio | Dict `out` con `status` y un campo por componente | `backend/src/rag/api/main.py` | No prueba DeepSeek; colección ausente se informa como Qdrant caído |
| 14 | Bucles de reintento | Contador `attempt` / `intento` | `embedder.py`, `extraer_figuras.py` | Sanos en el proceso; sin límite entre procesos (ver 7) |

---

## 1. Ciclo de vida de un documento en el índice

**Propósito.** En cada corrida de `sein-rag-ingest` decide si un documento del
vault se salta, se reindexa, falla o se purga.

**Dónde.**

- `backend/src/rag/ingestion/indexer.py:262` `ingest_vault`.
- `indexer.py:283` lee `existing` (doc_id a `source_hash`) desde Qdrant, también con `--force`.
- `indexer.py:293-298` un documento que no carga cuenta como fallido y como visto.
- `indexer.py:306-312` condición de salto.
- `indexer.py:326-329` embeber, borrar la versión vieja y hacer upsert.
- `indexer.py:357` cálculo de `stale`; `indexer.py:362-405` `_purge_stale` con sus frenos.
- `indexer.py:64-66` `IngestStats.ok`.
- `backend/src/rag/ingestion/loader.py:51-62` `load_document` devuelve `None` si no lee el fichero o el cuerpo está vacío; `loader.py:64-70` hash de respaldo sin `source_hash`.
- `backend/src/rag/ingestion/chunker.py:44-45` id `uuid5(doc_id|source_hash|i)`.
- `backend/src/rag/ingestion/cli.py:79` `--allow-purge` sube el umbral a 1.0; `cli.py:98` código de salida.

**Codificación.** No hay campo de estado. El estado de un documento es la
relación entre tres hechos: el `.md` existe y tiene cuerpo, el mapa `existing`
que se reconstruye del payload de Qdrant, y los flags de la corrida (`force`,
`forzados`, `max_purge_ratio`). El veredicto de la corrida combina dos
contadores: `failed == 0 and purge_skipped == 0`.

**Estados y transiciones.**

| Estado | Condición | Disparador de salida |
|---|---|---|
| Nuevo | `.md` legible, doc_id fuera de `existing` | Corrida: pasa a Reindexando |
| AlDia | Mismo hash en vault e índice | Hash distinto, `--force` o doc en `--reindexar-lista` |
| Cambiado | Hash distinto | Corrida: pasa a Reindexando |
| Reindexando | Transitorio: chunk, embed, delete, upsert | Éxito: AlDia. Excepción: Fallido |
| FallidoSinVersion | Era Nuevo y falló | Siguiente corrida lo reintenta |
| FallidoConVersionVieja | Tenía versión y falló antes del borrado | Siguiente corrida lo reintenta; la versión vieja sigue servida |
| Zombi | Indexado; el `.md` existe pero queda vacío o ilegible | Solo si alguien arregla o borra el fichero |
| Huerfano | En el índice, sin `.md` | Purga, o Retenido por los frenos |
| PurgaRetenida | Vault vacío, o más del 50 % sin `--allow-purge` | Corrida con `--allow-purge` y vault no vacío |
| Purgado | Terminal | Ninguno |

```mermaid
stateDiagram-v2
    [*] --> Nuevo: aparece el md
    Nuevo --> Reindexando: corrida
    AlDia --> Reindexando: hash distinto o force o lista
    Reindexando --> AlDia: embed y upsert correctos
    Reindexando --> FallidoSinVersion: error y no estaba indexado
    Reindexando --> FallidoConVersionVieja: error antes del borrado
    FallidoSinVersion --> Reindexando: siguiente corrida
    FallidoConVersionVieja --> Reindexando: siguiente corrida
    AlDia --> Zombi: md vacío o ilegible
    Zombi --> Reindexando: md reparado
    Zombi --> Huerfano: md borrado
    AlDia --> Huerfano: md borrado o renombrado
    Huerfano --> Purgado: frenos no activos
    Huerfano --> PurgaRetenida: vault vacío o ratio mayor que 0.5
    PurgaRetenida --> Purgado: allow-purge con vault no vacío
    PurgaRetenida --> AlDia: el md vuelve
    Purgado --> [*]
```

**Invariantes.**

- Una sola generación por doc_id. Se cumple: el borrado por doc_id va antes del upsert (`indexer.py:327-329`) y `existing` se consulta siempre (`indexer.py:283`).
- Embeber antes de borrar (`indexer.py:326`). Se cumple: un fallo del embedder deja la versión vieja.
- Un documento presente pero ilegible no se purga (`indexer.py:298`). Se cumple.
- Frenos de purga: vault vacío (`indexer.py:380`) y ratio (`indexer.py:389-390`). Se cumplen. `--allow-purge` no salta el freno del vault vacío.
- Ids deterministas (`chunker.py:44-45`). Se cumple.
- `source_hash` describe el contenido del `.md`. **No se cumple**: es el hash del PDF. Ver máquina 2.

**Riesgos.**

1. *verificado*. Ventana de ausencia. Entre el borrado con `wait=True` (`indexer.py:328`) y el upsert (`indexer.py:329`) el documento no existe para la API. Con `--force` la ventana se repite una vez por documento. Una consulta en ese momento no ve el documento, y `find_extreme_docs` (`backend/src/rag/retrieval/store.py:262`) puede elegir otro como "el más reciente".
2. *verificado*. Estado Zombi. Un documento indexado cuyo `.md` queda vacío conserva su versión vieja sin límite de tiempo, porque se marca como visto (`indexer.py:298`). Cada corrida sale con 1. El E2E prueba un vacío nuevo (`backend/e2e/run_e2e.py:475-493`), sin versión previa.
3. *verificado*. Renombrado masivo. Si cambian más de la mitad de los doc_id (por ejemplo, al reorganizar carpetas), las versiones nuevas se indexan dentro del bucle y la purga posterior se retiene. El índice queda con dos copias de cada contrato bajo doc_id distintos. El código de salida 1 avisa, pero el retrieval ya sirve duplicados.
4. *hipótesis*. Caída a mitad de upsert. Si Qdrant aplica solo parte de los puntos, `existing` ya muestra el hash nuevo y la siguiente corrida salta el documento incompleto. El código no compara el número de chunks. Una caída o una excepción entre borrado y upsert sí se repara sola: el documento sale de `existing` y vuelve como Nuevo en la siguiente corrida. Hasta entonces (30 min con launchd) el documento falta en el índice.
5. *hipótesis*. Corridas concurrentes. No hay cerrojo. launchd lanza la ingesta cada 30 min (`docs/DEPLOYMENT.md`) y `aplicar_oleada.sh` lanza otra `docker compose run`. Con el mismo vault convergen por los ids deterministas. Con un vault que cambia en medio (rsync), el resultado depende del orden.
6. *verificado*. El manifiesto solo registra `indexed` y `failed` (`indexer.py:339`, `indexer.py:352`). Saltos, purgas y purgas retenidas no dejan línea. Ningún código lee el manifiesto.
7. *verificado*. Un doc_id de `--reindexar-lista` que no existe en el vault se ignora sin aviso (`cli.py:55-60`, `indexer.py:306-309`).

---

## 2. Versión de un documento entre Khipu, Mac e índice

**Propósito.** Llevar un `.md` enriquecido desde el vault de Khipu hasta el
índice que consulta la API.

**Dónde.**

- `tools/figuras/fusionar_figuras.py:280-284` escritura del `.md` en Khipu.
- `fusionar_figuras.py:391-401` listas `docs_a_reindexar.txt` y `docs_con_figura.txt`.
- `fusionar_figuras.py:85-99` sello `figuras_leidas` en el frontmatter.
- `tools/figuras/aplicar_oleada.sh:29-35` fusión; `:45-49` rsync; `:57-78` elección de la lista; `:86-88` reindexado; `:92` verificación.
- `tools/figuras/cierre.sh:34-36` restauración; `:41-47` fusión; `:59-60` reconstrucción del backend; `:64-73` reindexado por sello.
- `backend/src/rag/ingestion/indexer.py:306-312` salto por hash; `indexer.py:215-240` payload sin hash del `.md` ni sello.

**Codificación.** No existe un registro central. El estado se reparte entre el
`.md` de Khipu, su copia en el Mac (un rsync externo cada 2 h, según
`docs/FIGURAS.md`), el sello del frontmatter, dos listas `.txt` que cada
oleada sobrescribe y el payload de Qdrant. El payload guarda `source_hash`, que
es el hash del PDF. Ese hash no cambia con el enriquecimiento.

**Estados y transiciones.**

```mermaid
stateDiagram-v2
    state "Virgen sincronizado" as V0
    state "Enriquecido en Khipu" as K1
    state "Enriquecido en Mac, índice viejo" as M1
    state "Sincronizado enriquecido" as S
    state "Fuera de toda lista" as P
    state "Restaurado por el cierre" as R
    [*] --> V0
    V0 --> K1: fusión con aplicar
    K1 --> M1: rsync
    M1 --> S: reindexar lista correcto
    M1 --> P: reindexado falla y llega otra oleada
    P --> S: modo sello si lleva sello
    P --> S: ingesta con force
    S --> K1: oleada nueva añade bloques
    S --> R: cierre restaura respaldo
    R --> K1: cierre fusiona de nuevo
    S --> V0: OCR reescribe el md
```

- Un documento que solo pierde marcadores falsos llega a M1 sin sello.
- El modo por defecto reindexa `docs_con_figura.txt`. El modo `todo` reindexa `docs_a_reindexar.txt`.
- El modo `sello` y el cierre reindexan solo los documentos con `figuras_leidas`.

**Invariante.** El índice refleja el vault. **No se cumple de forma
automática**: la ingesta normal salta el documento porque el hash del PDF no
cambió (`indexer.py:306-312`). Solo `--reindexar-lista` o `--force` mueven el
documento al índice.

**Riesgos.**

1. *verificado*. El fallo del reindexado se oculta. `aplicar_oleada.sh:86-88` y `cierre.sh:71-73` pasan la ingesta por `| tail`. Con zsh y sin `pipefail`, el estado de la tubería es el de `tail`. El propio script describe este fallo para rsync (`aplicar_oleada.sh:41-44`) y lo repite con la ingesta. El código de salida final es el de `verificar_enriquecimiento.py`, que mira el vault y no el índice.
2. *verificado*. Los documentos que solo pierden marcadores quedan en P. `docs_a_reindexar.txt` se calcula contra el vault actual (`fusionar_figuras.py:280`, `:391-393`). En la oleada siguiente ese documento ya no cambia y no aparece. El modo `sello` y el cierre (`cierre.sh:64-67`) tampoco lo incluyen, porque no lleva sello. El índice conserva los `![image](url)` alucinados de esos documentos hasta un `--force`.
3. *verificado*. Ningún proceso responde a la pregunta "qué versión del `.md` tiene el índice". El payload no guarda un hash del `.md` ni el sello (`indexer.py:215-240`).
4. *hipótesis*. `cierre.sh:34` restaura el respaldo más antiguo. Si el OCR reescribió un documento después del respaldo, el cierre lo devuelve a la versión anterior con su `source_hash` anterior. La ingesta normal lo ve Cambiado y reindexa el contrato viejo. Los PDF del registro no se editan en operación normal (`docs/FIGURAS.md`, "Qué sobrevive a qué"), así que la probabilidad es baja.
5. *hipótesis*. Ventana del cierre. Entre la restauración (`cierre.sh:36`) y la fusión (`cierre.sh:41-47`) el vault de Khipu está virgen. Si el rsync periódico corre en ese momento, el Mac recibe el vault virgen. La ingesta periódica salta por hash. Un reindexado manual en esa ventana indexaría la versión virgen.
6. *verificado*. `cierre.sh:59-60` reconstruye y reinicia el backend. Esa acción corta cualquier stream SSE en curso.
7. *verificado*, menor. `figuras_leidas` guarda las inserciones de la pasada actual (`fusionar_figuras.py:277-278`). Un documento que gana una figura en la segunda oleada pasa de 3 a 1.

---

## 3. Bucle agéntico y resultado de la respuesta

**Propósito.** Llevar una pregunta por análisis, recuperación, evaluación,
reformulación, generación y verificación, o cortarla antes.

**Dónde.**

- `backend/src/rag/graph/agent.py:721-749` construcción del grafo.
- `agent.py:274-377` `analyze`; `agent.py:291` cinturón de alcance; `agent.py:296` filtros de la UI sobre los extraídos; `agent.py:326-361` selector temporal.
- `agent.py:379-427` `retrieve` con dos modos y dos reintentos sin filtros (`agent.py:392-396`, `agent.py:417-426`).
- `agent.py:429-461` `grade`; `agent.py:431` omite el grading en modo multi-documento; `agent.py:454` fallback permisivo.
- `agent.py:463-477` `rewrite`; `agent.py:691-697` `after_grade`.
- `agent.py:681-687` `no_context`; `agent.py:710-719` `refuse`.
- `backend/src/rag/graph/state.py:10-37` `AgentState`, `TypedDict` con `total=False`.
- `backend/src/rag/config.py:74` `max_query_rewrites = 2`.

**Codificación.** La topología es explícita: un `StateGraph`. El resultado es
implícito. Lo define la combinación de `scope`, `no_context`, `grounded`,
`claims_total`, `claim_issues` y `relevant_documents`. El modo del bucle vive
en el contador `rewrites` y en si `relevant_documents` está vacío.

**Estados y transiciones.**

```mermaid
stateDiagram-v2
    [*] --> Analizando
    Analizando --> Rechazado: scope distinto de contratos
    Analizando --> Recuperando: scope contratos
    Recuperando --> Evaluando
    Evaluando --> Generando: hay fragmentos relevantes
    Evaluando --> Reformulando: ninguno y rewrites menor que 2
    Evaluando --> SinContexto: ninguno y rewrites igual a 2
    Reformulando --> Recuperando: rewrites más 1
    Generando --> Verificando
    Verificando --> [*]
    SinContexto --> [*]
    Rechazado --> [*]
    Analizando --> Error: excepción del LLM
    Recuperando --> Error: excepción de Qdrant u Ollama
    Evaluando --> Error: excepción del LLM
    Generando --> Error: excepción del LLM
    Error --> [*]
```

Resultados terminales, tal como llegan en el evento `end`:

| Resultado | `grounded` | `no_context` | `claims_total` | `claim_issues` |
|---|---|---|---|---|
| Rechazo (fuera de tema o volcado) | `null` | `false` | 0 | vacío |
| Sin contexto | `true` | `true` | 0 | vacío |
| Verificada | `true` | `false` | mayor que 0 | vacío |
| Refutada | `false` | `false` | mayor que 0 | con alguna `refutada` |
| No concluyente | `null` | `false` | mayor que 0 | sin `refutada` |
| Sin verificar | `null` | `false` | 0 | vacío |

**Invariantes.**

- El bucle termina. Se cumple: `after_grade` corta en `max_query_rewrites` (`agent.py:694`).
- Los filtros de la UI mandan y nunca se relajan. Se cumple: `agent.py:296`, `agent.py:392`, `agent.py:419-421`.
- El grading ante una respuesta ilegible del modelo deja pasar el fragmento (`agent.py:454`). Se cumple.

**Riesgos.**

1. *verificado*. `no_context` devuelve `grounded: true` (`agent.py:686`). El frontend lo filtra porque exige `total > 0` para la insignia (`frontend/src/components/Message.tsx:43`). `backend/eval/run_eval.py:56` suma `bool(grounded)`: cada "no está en el índice" cuenta como respuesta fundamentada. El grounded rate sube cuando el retrieval empeora.
2. *verificado*. Rechazo y Sin verificar tienen la misma combinación de campos. Solo el texto de la respuesta los separa. Un cliente distinto del frontend no puede saber que la verificación no corrió.
3. *verificado*. Con filtros de la UI que dejan cero resultados, `rewrite` cambia solo `search_query` (`agent.py:477`). Los filtros siguen. El agente gasta dos reformulaciones y dos búsquedas antes de Sin contexto. Esto ocurre con los puntos legados sin `*_norm` (máquina 11).
4. *verificado* el mecanismo, *hipótesis* el disparador. En modo multi-documento con selector temporal, el reintento sin filtros (`agent.py:392-396`) no comprueba si los `doc_id` vienen del selector. Si la búsqueda acotada devolviera vacío, el generador recibiría una `selector_note` que nombra N documentos y un contexto de otros. La búsqueda densa con filtro casi siempre devuelve puntos, así que el caso es raro.
5. *verificado*. No hay reintento ni fallback entre modelos o proveedores. El proveedor se fija al construir el agente (`backend/src/rag/llm.py:23`, `llm.py:41`). Solo `verify` atrapa excepciones (`agent.py:580`, `agent.py:606`). Un fallo en cualquier otro nodo termina en Error.
6. *verificado*. La búsqueda léxica cae a solo densa sin señal (`backend/src/rag/retrieval/store.py:249-251`). El modo degradado solo aparece como WARNING en el log. El agente, la API y el usuario no lo ven.

---

## 4. Veredicto por afirmación del verificador

**Propósito.** Contrastar cada afirmación de la respuesta contra el fragmento
que cita y resumir el resultado en `grounded`.

**Dónde.**

- `backend/src/rag/graph/agent.py:496-523` `_extract_claims`; `agent.py:506` tope `MAX_CLAIMS`; `agent.py:520` descarta citas fuera de rango.
- `agent.py:525-561` `_refute`; `agent.py:557` acepta estados de `CLAIM_STATES`.
- `agent.py:563-679` `verify`: `agent.py:576-584` salidas tempranas; `agent.py:591-595` asignación de la única fuente citada; `agent.py:621-638` segunda ronda; `agent.py:640-660` precedencia; `agent.py:661-667` veredicto global.
- `backend/src/rag/graph/prompts.py:243` `CLAIM_STATES`.

**Codificación.** Cada afirmación es un dict `{texto, citas}`. Sus veredictos
son una lista de tuplas `(estado, motivo)` acumulada en dos rondas. El estado
final sale de una regla de precedencia. El veredicto global es un booleano de
tres valores (`True`, `False`, `None`).

**Estados y transiciones.**

```mermaid
stateDiagram-v2
    [*] --> Extraida
    Extraida --> SinCita: citas vacías y varias fuentes citadas
    Extraida --> Ronda1: tiene citas
    Ronda1 --> Sustentada: algún veredicto sustentada
    Ronda1 --> Refutada: refutada y ninguna sustentada
    Ronda1 --> Ronda2: solo ausente o sin veredicto
    Ronda2 --> Sustentada: otra fuente citada la sostiene
    Ronda2 --> Refutada: otra fuente la contradice
    Ronda2 --> Ausente: sigue sin respaldo
    Ronda2 --> AusenteSinVeredicto: ningún veredicto válido
    Sustentada --> [*]
    Refutada --> [*]
    Ausente --> [*]
    AusenteSinVeredicto --> [*]
    SinCita --> [*]
```

Veredicto global: `True` si todas son Sustentada; `False` si alguna es
Refutada; `None` en el resto. También `None` con `claims_total = 0` cuando no
hay documentos, no hay respuesta, el extractor falla o no devuelve nada.

**Invariantes.**

- Lo refutado no se reabre en la segunda ronda (`agent.py:624`). Se cumple.
- Una cita fuera de rango se descarta (`agent.py:520`). Se cumple.
- Un fallo del refutador nunca tumba la respuesta (`agent.py:606-611`). Se cumple, y degrada hacia No concluyente.

**Riesgos.**

1. *verificado*. Tope silencioso. `raw[:MAX_CLAIMS]` (`agent.py:506`) corta en 25. Si las 25 primeras se sostienen, `grounded` es `True` y la insignia dice "25 afirmaciones contrastadas". La interfaz no avisa de las afirmaciones que no se contrastaron.
2. *verificado* el mecanismo, *hipótesis* el impacto. Precedencia Sustentada sobre Refutada (`agent.py:653-657`). Una afirmación que un fragmento contradice y otro sostiene termina Sustentada. Si el fragmento que la sostiene es de otro contrato con la misma cifra, la atribución errónea puede pasar.
3. *verificado*, menor. El refutador puede devolver `sin_cita` (`agent.py:557` lo acepta). Ese valor se mezcla con el `sin_cita` del extractor, aunque la afirmación sí tenía cita.
4. *verificado*. Sin verificar llega al frontend sin ninguna insignia (`Message.tsx:43-60`). El usuario no distingue una respuesta que nadie verificó de una respuesta sin datos.

---

## 5. Protocolo SSE de `/api/chat`

**Propósito.** Transmitir el progreso del agente, las fuentes, los tokens y el
resultado final en un solo stream.

**Dónde.**

- `backend/src/rag/api/main.py:327` ruta con la dependencia `chat_access`; `main.py:185-213` cuota.
- `main.py:331-386` `event_stream`: `main.py:340-341` `final` y `sources_sent`; `main.py:346-347` `status`; `main.py:348-356` fusión de salidas y `sources`; `main.py:357-364` `token`; `main.py:365-379` `end`; `main.py:380-386` `error`.
- `main.py:40-49` `STEP_LABELS` filtra los nombres de nodo.
- `frontend/src/api.ts:39-97` lector del stream; `frontend/nginx.conf:94-106` proxy sin buffering.

**Codificación.** No hay un tipo para la gramática del stream. El orden sale
de los eventos `on_chain_start` y `on_chain_end` de LangGraph filtrados por
nombre. Un flag local (`sources_sent`) evita repetir las fuentes. Un dict local
(`final`) reconstruye el estado del grafo fusionando la salida de cada nodo.

**Secuencias legales.**

- Antes del stream: respuesta HTTP 401, 422, 429 o 5xx sin SSE.
- Rechazo: `status(analyze)`, `status(refuse)`, `end`.
- Sin contexto: `status(analyze)`, de una a tres veces `status(retrieve) status(grade)` con `status(rewrite)` entre ellas, `status(no_context)`, `end`.
- Respuesta: igual hasta el último `grade`, luego `sources`, `status(generate)`, cero o más `token`, `status(verify)`, `end`.
- Cualquier prefijo puede terminar en `error`.

```mermaid
stateDiagram-v2
    [*] --> Analyze: status analyze
    Analyze --> Refuse: status refuse
    Refuse --> End: end
    Analyze --> Retrieve: status retrieve
    Retrieve --> Grade: status grade
    Grade --> Rewrite: status rewrite
    Rewrite --> Retrieve: status retrieve
    Grade --> NoContext: status no_context
    NoContext --> End: end
    Grade --> Sources: sources una sola vez
    Sources --> Generate: status generate
    Generate --> Generate: token
    Generate --> Verify: status verify
    Verify --> End: end
    Analyze --> Err: error
    Retrieve --> Err: error
    Grade --> Err: error
    Generate --> Err: error
    Verify --> Err: error
    End --> [*]
    Err --> [*]
```

**Invariantes.**

- Un solo evento terminal (`end` o `error`). Se cumple por la estructura del generador, salvo desconexión o proceso muerto.
- `sources` como máximo una vez y antes de `generate` (`main.py:352`). Se cumple: un `grade` con fragmentos siempre lleva a `generate`.
- `end.sources` coincide con `relevant_documents` final (`main.py:372`). Se cumple.

**Riesgos.**

1. *verificado*. La cuota se consume antes del stream: `chat_access` corre como dependencia (`main.py:327`, `main.py:203`). Un error del agente, un corte de red o "Detener" no devuelven el intento.
2. *hipótesis*. Desconexión del cliente. Starlette cancela el generador. `except Exception` (`main.py:380`) no atrapa la cancelación, así que no se emite `error` y la cancelación llega a la llamada httpx en curso. Las búsquedas que corren en `asyncio.to_thread` (`agent.py:379-427`) terminan en su hilo. Si Ollama detiene la generación al cerrarse la conexión depende de Ollama.
3. *verificado*. `final` duplica el estado del grafo (`main.py:351`). Hoy funciona porque cada nodo devuelve un parche plano sin reducers. Si un nodo futuro usa un reducer de lista, o un sub-runnable lleva el nombre de un paso, `final` diverge del estado real sin ningún error.
4. *verificado*, menor. El texto que llega en tokens puede diferir de `end.answer`, porque `strip_ghost_citations` (`agent.py:494`) quita marcadores `[n]` que ya se mostraron. El frontend reemplaza el contenido al recibir `end` (`frontend/src/App.tsx:132-145`). El usuario ve desaparecer una cita.
5. *verificado*. El lector del frontend descarta en silencio cualquier excepción del manejador de eventos, además de los frames corruptos: el `try` envuelve `onEvent(JSON.parse(...))` (`frontend/src/api.ts:90-94`).

---

## 6. Mensaje del asistente en el frontend

**Propósito.** Mostrar una respuesta mientras llega y después de terminar.

**Dónde.**

- `frontend/src/types.ts:23-35` `ChatMessage`.
- `frontend/src/App.tsx:77-172` `send`: `App.tsx:80` guarda de `busy`; `App.tsx:86-88` historial; `App.tsx:90-94` placeholder; `App.tsx:117` y `App.tsx:153-159` flag `finished`; `App.tsx:160-165` abort y errores; `App.tsx:174` `stop`; `App.tsx:176-185` `nuevaConversacion`.
- `frontend/src/components/Message.tsx:23`, `:34`, `:41`, `:43`, `:52`, `:61`, `:74` derivación visual.

**Codificación.** Campos opcionales de `ChatMessage`: `streaming`, `status`,
`error`, `content`, `grounded`, `claimsTotal`, `claimIssues`, `noContext`. La
combinación es el estado. `busy` y `abortRef` en `App` guardan el estado de la
petición.

**Estados y transiciones.**

| Estado | Campos |
|---|---|
| Conectando | `streaming`, `status = "Conectando"`, `content = ""` |
| Trabajando | `streaming`, `status` del paso, `content = ""` |
| Escribiendo | `streaming`, `content` no vacío |
| Completo | `streaming = false`, sin `error`, valores de `end` |
| Error | `error` definido (evento `error`, HTTP o red) |
| Cortado | `error` de "La conexión se cortó" (sin terminal) |
| Detenido | `streaming = false`, sin `error`, contenido parcial o vacío |

```mermaid
stateDiagram-v2
    [*] --> Conectando: send
    Conectando --> Trabajando: status
    Trabajando --> Trabajando: status o sources
    Trabajando --> Escribiendo: token
    Escribiendo --> Escribiendo: token o status
    Trabajando --> Completo: end
    Escribiendo --> Completo: end
    Conectando --> Error: HTTP 401 429 o 5xx
    Trabajando --> Error: evento error
    Escribiendo --> Error: evento error
    Escribiendo --> Cortado: stream sin terminal
    Trabajando --> Cortado: stream sin terminal
    Trabajando --> Detenido: Detener
    Escribiendo --> Detenido: Detener
    Completo --> [*]
    Error --> [*]
    Cortado --> [*]
    Detenido --> [*]
```

**Invariantes.**

- Una sola petición en curso: `busy` (`App.tsx:80`). Se cumple.
- Todo stream termina en un estado no `streaming`: `finished` (`App.tsx:153-159`). Se cumple.

**Riesgos.**

1. *verificado*. Detenido se ve como Completo. `App.tsx:163-165` pone `streaming: false` sin `error`. `Message.tsx:41` muestra el pie con el botón de fuentes y ninguna marca de "detenida". El texto parcial entra al historial del turno siguiente como respuesta del asistente (`App.tsx:86-88`). Si el usuario detuvo antes del primer token, el historial lleva un turno de asistente vacío.
2. *verificado*. Un Error saca del historial la respuesta pero deja la pregunta (`App.tsx:87`). El turno siguiente envía dos mensajes de usuario seguidos.
3. *verificado*, menor. El paso del agente se oculta en cuanto hay texto (`Message.tsx:23`). Durante `verify` el usuario ve el cursor y ningún indicador del paso.
4. *verificado*, sin impacto. `nuevaConversacion` con un stream activo vacía la lista y aborta. El `catch` posterior llama a `updateLast` sobre una lista vacía y escribe el índice `-1`. React lo ignora.

---

## 7. Cadena SLURM de lectura y verificación

**Propósito.** Leer con el modelo de visión todas las páginas del worklist y
contrastar las lecturas de dibujos, en jobs de 8 h encadenados.

**Dónde.**

- `tools/figuras/figuras_job.slurm:31-50` `pendientes()`; `:59-66` decisión de seguir; `:70` sucesor con `afterany`; `:76-99` arranque de vLLM y sus salidas con 1; `:109-110` lectura con deadline; `:121` segunda pasada; `:127` salida.
- `tools/figuras/extraer_figuras.py:118-131` `cargar_hechos`; `:251-287` tres intentos por página; `:350-354` corte por deadline.
- `tools/figuras/verificar_lecturas.py:94-105` `hechos`; `tools/figuras/pendientes_verificacion.py:31-41`.

**Codificación.** No hay cola persistente. El estado de la cadena es el par
(páginas por leer, lecturas por verificar). Cada job lo recalcula desde
`worklist.jsonl`, `figuras.jsonl` y `verificacion.jsonl`. Una fila con `error`
no cuenta como hecha. El otro componente del estado es si hay un sucesor en la
cola de SLURM.

**Estados y transiciones.**

```mermaid
stateDiagram-v2
    [*] --> Comprobando: job arranca
    Comprobando --> Fin: nada por leer ni verificar
    Comprobando --> Encadenado: hay trabajo y se registra sucesor
    Encadenado --> ArrancandoVllm
    ArrancandoVllm --> VllmCaido: proceso muerto o timeout
    ArrancandoVllm --> Leyendo: health correcto
    Leyendo --> Verificando: fin de pendientes o deadline
    Verificando --> JobTerminado
    VllmCaido --> Comprobando: sucesor arranca
    JobTerminado --> Comprobando: sucesor arranca
    Leyendo --> Matado: TIMEOUT de SLURM
    Matado --> Comprobando: sucesor arranca
    Fin --> [*]
```

**Invariantes.**

- El sucesor se registra antes del trabajo (`figuras_job.slurm:70`), así sobrevive a un SIGKILL. Se cumple.
- Una página con error se reintenta (`figuras_job.slurm:47`, `extraer_figuras.py:129`). Se cumple.
- La cadena termina cuando no queda trabajo (`figuras_job.slurm:62-66`). **Solo se cumple si todo error es transitorio.**

**Riesgos.**

1. *verificado*. No hay estado terminal para una página que falla siempre. Una fila de error nunca cuenta como hecha y no hay contador de intentos entre jobs. El script reconoce "unas pocas páginas que fallan siempre" (`figuras_job.slurm:117-119`). Con una sola de ellas, cada job carga el modelo de 27B, reintenta esa página tres veces y encadena otro job. El error `sin_imagen` (`extraer_figuras.py:184`) tampoco cambia sin intervención.
2. *verificado*. vLLM que no arranca sale con 1 (`figuras_job.slurm:95`, `:99`) después de registrar el sucesor (`:70`). Un fallo persistente (modelo ausente, GPU) produce una cadena sin fin de jobs fallidos.
3. *verificado*. La segunda pasada tiene la misma regla: las filas de error de `verificacion.jsonl` no cuentan (`pendientes_verificacion.py:40`). Una lectura que el revisor no logra procesar mantiene `VER` por encima de 0.
4. *hipótesis*. Tolerancia desigual a líneas corruptas. `extraer_figuras.py:118-131` y `pendientes()` ignoran una línea JSON rota. `verificar_lecturas.py:179` y `fusionar_figuras.py:316`, `:327` usan `json.loads` sin `try`. Una línea truncada por un SIGKILL en mitad de una escritura hace fallar la verificación en cada job, `VER` no baja y la cadena sigue. La misma línea bloquea la fusión.
5. *documentado* en `docs/FIGURAS.md`. El sucesor queda congelado con el `.slurm` del momento del `sbatch`.

---

## 8. Estado de una página en el pipeline de figuras

**Propósito.** Seguir cada par (documento, página) desde la detección hasta la
inserción en el vault.

**Dónde.**

- `tools/figuras/prep_figuras.py:34-68` inventario por marcadores; `:70-87` render; `:93-95` escritura del worklist.
- `tools/figuras/render_pendientes.py:20-33` render sin rehacer el worklist.
- `tools/figuras/extraer_figuras.py:182-185` imagen y modo rescate.
- `tools/figuras/verificar_lecturas.py:175-189` candidatas.
- `tools/figuras/fusionar_figuras.py:172-196` `util`; `:225-232` decisión por página.

**Codificación.** Presencia de artefactos con la misma clave `doc__pN`: fila en
`worklist.jsonl`, `imgs/<clave>.jpg`, filas en `figuras.jsonl` (append-only,
varias por clave posibles), filas en `verificacion.jsonl`, y la cabecera del
bloque dentro del `.md`. El modo rescate es el prefijo del campo `motivo`.

**Estados y transiciones.**

```mermaid
stateDiagram-v2
    [*] --> Detectada: marcador en el md
    Detectada --> SinPdf: falta el PDF
    Detectada --> ErrorRender: pdftoppm falla
    Detectada --> Renderizada: jpg en disco
    ErrorRender --> Renderizada: render_pendientes
    Renderizada --> LeidaConError: tres intentos fallidos
    LeidaConError --> Renderizada: siguiente job
    Renderizada --> Leida: fila sin error
    Leida --> PorVerificar: tipo de dibujo y aporta
    PorVerificar --> Verificada: fila sin error
    Leida --> Descartada: no útil en la fusión
    Leida --> Insertada: útil en la fusión
    Verificada --> Insertada: útil, con tachados
    Insertada --> YaInsertada: oleada siguiente
    Descartada --> [*]
    YaInsertada --> [*]
```

**Riesgos.**

1. *verificado*. `prep_figuras.py:93` reescribe el worklist entero desde los marcadores del vault. La fusión borra todos los marcadores de cada documento que procesa, también los de páginas aún sin lectura (`fusionar_figuras.py:261-264`). Un nuevo `prep` después de una oleada saca esas páginas del worklist. Además, `prep` nunca escribe `motivo` (`prep_figuras.py:58-66`). Las filas de rescate que lee `extraer_figuras.py:185` vienen de un proceso que no está en el repositorio, y un nuevo `prep` las pierde.
2. *hipótesis*. Imagen parcial. El render da por hecha cualquier `.jpg` existente (`prep_figuras.py:76`, `render_pendientes.py:22`). Un `pdftoppm` muerto a mitad de escritura deja una imagen truncada que nadie vuelve a renderizar.
3. *verificado* el mecanismo, *hipótesis* el disparador. Si una clave tiene dos lecturas correctas, la fusión toma la última (`fusionar_figuras.py:225`) y la verificación salta la clave ya revisada (`verificar_lecturas.py:188-189`). La verificación de la lectura vieja se aplica a la nueva. `marcar_dudosas` lista como aviso incluso los fragmentos que ya no aparecen (`fusionar_figuras.py:111-114`).

---

## 9. Fusión en el vault por oleadas

**Propósito.** Insertar las lecturas útiles en el `.md` y borrar los
marcadores falsos, de forma repetible mientras la extracción avanza.

**Dónde.**

- `tools/figuras/fusionar_figuras.py:34-37` `YA_INSERTADO_RE`.
- `fusionar_figuras.py:211-294` `procesar_documento`: `:229-230` página ya insertada; `:237-238` variables `ya_insertado` y `pendiente`; `:240-250` bloque al cerrar la página; `:253-272` recorrido por líneas; `:282-284` escritura atómica por fichero.
- `fusionar_figuras.py:340-356` respaldo único del vault virgen.
- `tools/figuras/README.md:72-80` motivo del cierre.

**Codificación.** El propio texto del `.md` es el estado. La cabecera
`**Figura de la página N leída…**` o `**Página N recuperada…**` marca
"insertado". La ausencia de `![...]` marca "procesado". Dos variables locales
(`pendiente`, `ya_insertado`) llevan el modo de una línea a la siguiente.

**Estados por página dentro del `.md`.**

```mermaid
stateDiagram-v2
    [*] --> ConMarcador
    ConMarcador --> ConBloque: fusión y lectura útil
    ConMarcador --> SinMarcador: fusión sin lectura útil o sin lectura
    SinMarcador --> ConBloque: oleada posterior con lectura útil
    state ConBloque {
        [*] --> FormatoNuevo
        [*] --> FormatoViejo
    }
    ConBloque --> ConBloque: oleada posterior se salta la página
    ConBloque --> ConMarcador: cierre restaura el respaldo
    SinMarcador --> ConMarcador: cierre restaura el respaldo
```

**Invariantes.**

- Idempotencia por cabecera (`fusionar_figuras.py:229`). Se cumple.
- Escritura atómica por fichero (`fusionar_figuras.py:282-284`). Se cumple.
- El respaldo es el vault virgen. **No se comprueba**: la fusión lo crea solo si no existe ninguno en `--salida` (`fusionar_figuras.py:346-348`).

**Riesgos.**

1. *verificado*. Un bloque de formato viejo no tiene salida dentro de las oleadas. La fusión lo salta (`fusionar_figuras.py:229-230`) aunque cambie el formato. Solo `cierre.sh` lo rehace. Mientras tanto el chunker trata el bloque sin cierre como abierto hasta el final del documento (máquina 10).
2. *verificado*. Si la primera fusión con `--aplicar` corre con un `--salida` nuevo, o alguien limpia ese directorio, el respaldo "virgen" ya está enriquecido. `cierre.sh:34` restaura el respaldo más antiguo sin comprobarlo; solo imprime cuántos documentos llevan sello (`cierre.sh:37`).

---

## 10. Procedencia de las lecturas en el chunker

**Propósito.** Garantizar que todo fragmento con texto leído por el modelo de
visión lleve la cabecera de procedencia.

**Dónde.**

- `backend/src/rag/ingestion/chunker.py:148-181` `ensure_figuras_have_provenance`; `chunker.py:160-161` variables de modo; `chunker.py:172` condición de continuación; `chunker.py:179-181` cierre.
- `chunker.py:33-37` patrones de inicio y fin de bloque.
- `tools/figuras/verificar_enriquecimiento.py:78-83` comprobación sobre el corpus.

**Codificación.** Dos variables locales llevan el modo entre iteraciones:
`activa` (bloque abierto) y `ultima` (última cabecera vista).

**Estados y transiciones.**

```mermaid
stateDiagram-v2
    [*] --> Fuera
    Fuera --> Dentro: chunk con cabecera sin cierre posterior
    Fuera --> FueraConMemoria: chunk con cabecera y cierre
    Dentro --> Dentro: chunk sin cierre recibe cabecera
    Dentro --> FueraConMemoria: chunk con cierre recibe cabecera
    FueraConMemoria --> FueraConMemoria: chunk que arrastra el cierre recibe cabecera
    FueraConMemoria --> Dentro: chunk con cabecera nueva sin cierre
    FueraConMemoria --> FueraConMemoria: chunk de texto normal
```

**Invariante.** Ningún fragmento contiene lectura de máquina sin cabecera. Se
cumple para bloques con cierre.

**Riesgos.**

1. *verificado*. Un bloque sin cierre (formato viejo) deja `activa` fija hasta el final del documento, porque solo el cierre la vacía (`chunker.py:179-181`). Todo fragmento posterior, también el articulado del contrato, recibe la cabecera "(continuación)". El error va en la dirección prudente, pero contamina citas y atribuciones. `cierre.sh` lo resuelve al rehacer la fusión.
2. *verificado*. `verificar_enriquecimiento.py:81` solo comprueba fragmentos que contienen el cierre. Un fragmento intermedio de un bloque largo que perdiera la cabecera no se detecta. Hoy el chunker cubre ese caso; el verificador no protegería una regresión.

Fuera de estos dos puntos, la máquina es sana.

---

## 11. Esquema de puntos y colección en Qdrant

**Propósito.** Mantener la colección, sus índices y el formato del payload
compatibles con lo que espera el retrieval.

**Dónde.**

- `backend/src/rag/ingestion/indexer.py:69-86` `ensure_collection`; `indexer.py:78` solo crea si falta; `indexer.py:276-277` dimensión del embedder.
- `indexer.py:88-128` índices de payload; `indexer.py:130-180` `backfill_norm_fields`; `indexer.py:215-240` payload con `tipo` en minúsculas y espejos `*_norm`.
- `backend/src/rag/retrieval/store.py:37-69` filtros: `*_norm` (`store.py:44-53`) y doble grafía de `tipo` (`store.py:59-66`).
- `backend/src/rag/config.py:37` modelo de embeddings.
- `backend/src/rag/api/main.py:266-287` health.

**Codificación.** No hay campo de versión. La versión de cada punto se infiere
de la presencia de `*_norm` y de la grafía de `tipo`. La dimensión de la
colección queda fija al crearla. Ningún sitio guarda el modelo de embeddings.

**Estados.**

```mermaid
stateDiagram-v2
    state "Colección ausente" as Ausente
    state "Colección sin índices" as SinIndices
    state "Colección correcta" as Ok
    state "Puntos legados" as Legado
    state "Puntos actuales" as Actual
    state "Modelo distinto, misma dimensión" as MismaDim
    state "Modelo distinto, otra dimensión" as OtraDim
    [*] --> Ausente
    Ausente --> Ok: primera ingesta
    SinIndices --> Ok: cualquier ingesta reconcilia
    Ok --> Legado: puntos de versiones anteriores
    Legado --> Actual: reindexado del documento
    Ok --> MismaDim: cambio de RAG_EMBEDDING_MODEL
    Ok --> OtraDim: cambio de RAG_EMBEDDING_MODEL
    MismaDim --> Ok: borrar colección y reingestar
    OtraDim --> Ok: borrar colección y reingestar
```

**Riesgos.**

1. *verificado* el código, *hipótesis* la existencia de puntos legados. `backfill_norm_fields` (`indexer.py:130`) no tiene llamador en el repositorio. Un punto legado solo pasa a Actual si su documento se reindexa. Los filtros por razón social y suministrador buscan en `*_norm` (`store.py:44-53`) y no encuentran esos puntos. Con filtro de la UI no hay reintento sin filtro, y la pregunta termina en Sin contexto. El comentario de `store.py:60-62` sugiere que el índice histórico tiene puntos legados.
2. *verificado*. Cambio de modelo de embeddings. `ensure_collection` no recrea una colección existente (`indexer.py:78`), tampoco con `--force`. Con otra dimensión falla cada upsert, y cada búsqueda densa de la API lanza una excepción que termina en `error`. Con la misma dimensión, los documentos al día se saltan por hash, conservan vectores del modelo viejo, y la búsqueda densa compara vectores de dos modelos sin aviso. `/api/health` solo comprueba que el modelo exista en Ollama (`main.py:279-287`).
3. *verificado*. Con la colección ausente (despliegue nuevo antes de la primera ingesta), `store.count` lanza una excepción y `/api/health` informa `qdrant: error` (`main.py:266-272`) aunque Qdrant responda.

---

## 12. Cuota diaria por IP

**Propósito.** Limitar las preguntas sin clave a N por IP y día UTC.

**Dónde.**

- `backend/src/rag/api/quota.py:59-86` `hit`: `quota.py:69-74` UPSERT; `quota.py:76-84` rechazo sin inflar; `quota.py:98-106` poda una vez por día de proceso.
- `backend/src/rag/api/main.py:185-213` `chat_access`; `main.py:389-392` cabecera `X-Quota-Remaining`.
- `frontend/src/api.ts:63-75` lectura de la cuota; `frontend/src/App.tsx:28`, `:334` aviso.

**Codificación.** Filas `(ip, day, count)` en SQLite. El día es una cadena de
fecha UTC: el paso de día es implícito, porque cambia la clave. `_pruned_day`
recuerda la última poda.

```mermaid
stateDiagram-v2
    [*] --> SinFila
    SinFila --> Disponible: primer hit del día
    Disponible --> Disponible: hit con count menor que el límite
    Disponible --> Agotada: hit que alcanza el límite
    Agotada --> Agotada: hit rechazado, count se mantiene
    Disponible --> SinFila: cambia el día UTC
    Agotada --> SinFila: cambia el día UTC
```

**Invariantes.** Incremento atómico bajo cerrojo (`quota.py:67-74`). Los
rechazos no inflan el contador (`quota.py:76-84`). El contador sobrevive a un
redeploy (volumen `quota_state`). Se cumplen. La máquina del backend es sana.

**Riesgos.**

1. *verificado*. El intento se consume aunque la respuesta falle o el usuario la detenga (máquina 5, riesgo 1).
2. *verificado*. Tras guardar una API key, `quotaRemaining` conserva el último valor (`App.tsx:28`, `App.tsx:239`). Con clave válida el backend no envía `X-Quota-Remaining`, y `api.ts:74-75` solo actualiza cuando la cabecera existe. El aviso "Agotaste tus preguntas de hoy" sigue visible con acceso ilimitado hasta recargar.
3. *verificado*, menor. `Retry-After` vale siempre 86400 (`main.py:211`). No coincide con la medianoche UTC que anuncia el mensaje.

---

## 13. Salud del servicio

**Propósito.** Informar a Docker y al usuario si el backend puede responder.

**Dónde.** `backend/src/rag/api/main.py:244-295`; `backend/Dockerfile:52-53`;
`frontend/src/App.tsx:46`.

**Codificación.** Dict `out` con `status` (`ok` o `degraded`), un campo por
componente (`qdrant`, `ollama`, `missing_models`) y HTTP 200 o 503.

```mermaid
stateDiagram-v2
    [*] --> Ok
    Ok --> Degradado: Qdrant o colección fallan
    Ok --> Degradado: Ollama no responde
    Ok --> Degradado: falta un modelo requerido
    Degradado --> Ok: siguiente consulta sin fallos
```

**Riesgos.**

1. *verificado*. Con `RAG_LLM_PROVIDER=deepseek`, el health no prueba DeepSeek (`main.py:279-283`). Informa `ok` con el proveedor de generación caído.
2. *verificado*. El frontend lee la salud una sola vez al montar (`App.tsx:46`). El indicador no cambia durante la sesión.
3. *hipótesis*. El docstring de `main.py:248-251` espera que `restart: unless-stopped` reaccione a `unhealthy`. Docker sin Swarm no reinicia un contenedor por ese motivo; la marca solo informa.
4. Colección ausente informada como Qdrant caído: ver máquina 11, riesgo 3.

---

## 14. Bucles de reintento

**Dónde.**

- `backend/src/rag/ingestion/embedder.py:52-86` `_embed_batch`: contador `attempt`, backoff exponencial, sin espera tras el último intento.
- `tools/figuras/extraer_figuras.py:251-287` y `tools/figuras/verificar_lecturas.py:145-163`: tres intentos por página con espera lineal.

**Codificación.** Un contador local y una excepción guardada.

```mermaid
stateDiagram-v2
    [*] --> Intento
    Intento --> Exito: respuesta válida
    Intento --> Espera: error y quedan intentos
    Espera --> Intento
    Intento --> Agotado: error en el último intento
    Exito --> [*]
    Agotado --> [*]
```

**Valoración.** Dentro de un proceso los tres bucles son sanos y acotados. La
sonda de dimensión (`embedder.py:48`, llamada en `indexer.py:276`) falla antes
de tocar el índice si Ollama no responde, y la CLI sale con 1. El problema está
fuera del bucle: entre jobs SLURM el reintento no tiene límite (máquina 7). Las
llamadas al LLM del grafo no tienen reintento propio (máquina 3, riesgo 5).

---

## Patrones transversales

**P1. Estado como presencia más hash.** El sistema decide "hecho" por la
existencia de algo: un `.jpg`, una fila sin `error`, una cabecera en el
`.md`, un `source_hash` en el payload. Esto da idempotencia barata. Falla en
dos casos: cuando la presencia no implica completitud (imagen truncada,
upsert parcial) y cuando el hash no cubre el contenido real (hash del PDF
frente a `.md` enriquecido).

**P2. Errores sin contador.** "Una fila con error no está hecha" aparece en
`extraer_figuras.py`, `figuras_job.slurm`, `verificar_lecturas.py` y
`pendientes_verificacion.py`. La ingesta aplica la misma idea al documento
Zombi. Sin contador de intentos, un error permanente crea un estado sin salida
que se reintenta en cada pasada.

**P3. Listas de trabajo efímeras.** `worklist.jsonl`, `docs_a_reindexar.txt` y
`docs_con_figura.txt` se sobrescriben en cada pasada con lo que cambió en esa
pasada. Lo pendiente de pasadas anteriores se pierde. El modo `sello` corrige
esto para una parte de los documentos porque deriva la lista de un estado
durable (el frontmatter).

**P4. Booleanos de tres valores y campos opcionales.** `grounded: bool | None`
y los campos de `ChatMessage` codifican resultados distintos. Hay colisiones:
Sin verificar y Rechazo comparten campos; Detenido y Completo también;
Sin contexto lleva `grounded: true`.

**P5. Estado reconstruido en paralelo.** `final` en el SSE copia el estado del
grafo. `existing` en la ingesta copia el estado del índice al inicio de la
corrida. `pendientes()` en el `.slurm` reimplementa `cargar_hechos`. Cada copia
es correcta hoy y diverge en silencio si la fuente cambia de forma.

**P6. Códigos de salida perdidos.** Las tuberías `| tail` en `aplicar_oleada.sh`
y `cierre.sh` ocultan el resultado de la ingesta. El script corrigió ese fallo
para rsync y lo dejó para la ingesta.

**P7. Ventanas no atómicas entre procesos.** Borrado y upsert en Qdrant,
restauración y fusión en Khipu, fusión y rsync hacia el Mac. Ningún cerrojo
coordina la ingesta de launchd, la ingesta manual y la API.

**P8. Degradación silenciosa.** Búsqueda léxica que cae a solo densa,
verificación que no corre, puntos legados que los filtros no encuentran.
Cada caso solo deja una línea de log.

### Riesgos que más conviene atender

1. **Desfase entre vault e índice tras el enriquecimiento** (máquina 2). Guardar en el payload un hash del `.md` y compararlo en la ingesta normal. Quitar `| tail` de la ingesta en `aplicar_oleada.sh` y `cierre.sh`. Reindexar también los documentos que solo perdieron marcadores.
2. **Cadena SLURM sin estado terminal** (máquina 7). Contar intentos por clave y tratar como terminal una página que supera N fallos. No encadenar cuando vLLM no arranca.
3. **Señal de verificación engañosa** (máquinas 3 y 4). Devolver `grounded: null` en Sin contexto o excluirlo del eval. Avisar cuando el tope de 25 afirmaciones corta la verificación. Mostrar una marca cuando la verificación no corrió.
4. **Cambio de modelo de embeddings sin registro** (máquina 11). Guardar el modelo en la colección y negarse a indexar o a servir si no coincide.
5. **Respuesta detenida que parece completa** (máquina 6). Marcar el mensaje como detenido y no enviarlo como historial.
6. **Documento Zombi y renombrado masivo** (máquina 1). Decidir una salida para un `.md` vacío que tenía versión. Purgar antes de indexar cuando la purga se va a retener, o avisar de los duplicados.
7. **`prep_figuras.py` reescribe el worklist** (máquina 8). Impedir que una segunda corrida borre rescates y páginas ya sin marcador.
8. **Puntos legados sin `*_norm`** (máquina 11). Llamar a `backfill_norm_fields` desde la ingesta o eliminarlo.
