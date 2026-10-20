# Ashenmarch for Home Assistant

Your [Ashenmarch](https://ashenmarch.com) realm in Home Assistant: holdings, sieges, rank and your House's coat of arms as entities, plus an `ashenmarch_event` for every siege, lost or won holding, fading holding and filed march, so you can be notified when territory **near your zones** is under attack.

**Several players per household.** Each player adds their own token and gets their own House device (`sensor.house_cunningham_holdings`, `sensor.house_rival_holdings`, …). Every event says whose realm it is.

*[Español más abajo](#ashenmarch-para-home-assistant).*

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/cunningplay/ha-ashenmarch`, type **Integration**.
2. Install **Ashenmarch** and restart Home Assistant.
3. Settings → Devices & services → **Add integration** → **Ashenmarch**.

Requires Home Assistant 2026.10 or later.

## Setup

1. On [ashenmarch.com/portal/profile](https://ashenmarch.com/portal/profile), under **Personal access tokens**, create a token (name it "Home Assistant"). It starts with `am_pat_` and is shown **once**: copy it.
2. Paste it into the Ashenmarch setup in Home Assistant. The entry is named after your House.
3. Each player in the household repeats this with their own token. Adding the same player twice is refused.

The token is **read-only**: it can read your own realm and nothing else, and it can't change anything. Revoke it on the same page at any time; Home Assistant then asks for a new one for that player only.

## Entities

One device per player, named after the House ("House Cunningham").

| Entity | What it shows |
|---|---|
| `sensor.<house>_holdings` | Holdings you hold. Attributes: area in m², km² and mi². Its picture is your coat of arms. |
| `sensor.<house>_holdings_under_siege` | How many of them are contested right now. |
| `sensor.<house>_next_to_fade` | When your next holding decays (timestamp). |
| `sensor.<house>_rank` | Your position on your home city's board. Attributes: `region`, `title` (e.g. Baron). |
| `sensor.<house>_distance_this_week` | Distance marched since Monday, in your HA units. |
| `sensor.<house>_rank_progress` | Progress to the next rank, in %. Attributes: `rank_name`, `next_rank_name`, `points`, `next_threshold`, `next_area_m2`. Read hourly. |
| `sensor.<house>_area_ever_claimed` | All the area you've ever claimed (it never goes down). Attribute: `first_claim_at`. Read hourly. |
| `sensor.<house>_last_event` | When the last realm event happened. Attributes: `type`, and `recent`: the last 20 events, newest first, with the Herald's `message` and the territory's name (kept even when an event was outside your alert area; never coordinates). |
| `sensor.<house>_last_updated` | When the server computed the summary (diagnostic). |
| `binary_sensor.<house>_under_siege` | On while **any** holding in the realm is contested. |
| `binary_sensor.<house>_under_siege_nearby` | On while a siege is open **near one of your alert zones**. Attribute `zones`: which zones (e.g. `["Home"]`), `territories`: which holdings. |
| `image.<house>_coat_of_arms` | Your coat of arms. Downloaded by Home Assistant with your token and served through HA itself; your browser never talks to Ashenmarch. |

Home Assistant polls every 5 minutes (it backs off if the server is busy), and only downloads the summary and the crest again when they changed.

## Alerts near your zones

By default, alerts only fire for territory **within 25 miles of Home**, wide enough for a commute. Change it in **Configure** on the player's entry:

- **Alert area:** *Near my zones* (default) or *All territories*.
- **Zones:** one or more Home Assistant zones (Home, Work, a parent's house…). An alert fires if its territory is within the radius of **any** of them.
- **Radius:** in your Home Assistant units (miles or km, Settings → System → General). The default is **25 mi / 40 km with one zone** and **5 mi / 8 km with two or more**, and it follows the number of zones until you type your own radius. Clear the field to go back to the default.

The distance is measured from the zone's centre to the nearest edge of the territory, so a big territory that reaches into the area counts. `march_filed` always fires: it's your own march. Events outside the area are not lost; they're simply not announced (they show in the diagnostics), and switching to *All territories* applies from then on.

If every selected zone is deleted, alerts fall back to *All territories* and Home Assistant shows a repair notice.

## The `ashenmarch_event` event

Fired once per server event, in order, never twice (the feed cursor survives restarts).

| Field | |
|---|---|
| `type` | `siege_started`, `siege_ended` (`data.outcome`: `held` / `lost`), `holding_lost`, `holding_won`, `fading_soon` (`data.fades_at`), `march_filed` (`data.distance_m`, `data.area_new_m2`, …) |
| `message` | The Herald's line, already in the player's language. |
| `message_with_zone` | The same with the zone, ready for a notification: *"House Rival lays siege to Ashford Mill — near Home (3.2 mi)"*. |
| `player_id`, `house`, `device_id`, `config_entry_id` | Whose realm. |
| `zone`, `zone_name`, `distance`, `distance_unit`, `distance_km` | The nearest matching zone. In *All territories* mode: the nearest selected zone within 100 mi, or absent. |
| `zones_matched` | Every zone within the radius, with its distance. |
| `territory` | `{id, name, center, bbox}`. |
| `data`, `at`, `event_id` | The server's details. |

The House devices also offer **device triggers** (*Siege started*, *Holding lost*, *Fading soon*, *March filed*, …) for building automations without YAML.

## Example automations

**Notify the player whose land is under siege** (one automation per player, using their phone):

```yaml
automation:
  - alias: Ashenmarch — siege near me
    triggers:
      - trigger: event
        event_type: ashenmarch_event
        event_data:
          type: siege_started
          house: Cunningham
    actions:
      - action: notify.mobile_app_my_phone
        data:
          title: "⚔️ Siege"
          message: "{{ trigger.event.data.message_with_zone }}"
```

**Household:** any House under siege near a zone, flash the hall light:

```yaml
automation:
  - alias: Ashenmarch — flash the hall
    triggers:
      - trigger: event
        event_type: ashenmarch_event
        event_data:
          type: siege_started
    conditions:
      - condition: template
        value_template: "{{ trigger.event.data.zones_matched | count > 0 }}"
    actions:
      - repeat:
          count: 3
          sequence:
            - action: light.turn_on
              target: {entity_id: light.hall}
              data: {color_name: red, brightness_pct: 100}
            - delay: {milliseconds: 600}
            - action: light.turn_off
              target: {entity_id: light.hall}
            - delay: {milliseconds: 600}
```

**Household notification** naming the House:

```yaml
automation:
  - alias: Ashenmarch — any House under siege
    triggers:
      - trigger: event
        event_type: ashenmarch_event
        event_data:
          type: siege_started
    actions:
      - action: notify.family
        data:
          title: "⚔️ House {{ trigger.event.data.house }}"
          message: "{{ trigger.event.data.message_with_zone }}"
```

## Privacy

- The token is stored only in Home Assistant's config entry, never logged, and redacted in diagnostics.
- Home Assistant talks only to `api.ashenmarch.com`, and only to read the player's own realm.
- Distances to your zones are computed locally; your zones' coordinates never leave Home Assistant.

## Development

```bash
# Alert-area math (no Home Assistant needed)
uvx -p 3.14 --with pytest pytest -q tests
# Home Assistant integration tests against a fake Ashenmarch API
uvx -p 3.14 --with pytest-homeassistant-custom-component==0.13.372 pytest -q tests_ha -o asyncio_mode=auto
# hassfest
docker run --rm -v "$PWD:/github/workspace" ghcr.io/home-assistant/hassfest
```

## License

MIT. See [LICENSE](LICENSE).

---

# Ashenmarch para Home Assistant

Tu reino de [Ashenmarch](https://ashenmarch.com) en Home Assistant: dominios, asedios, posición y el escudo de armas de tu Casa como entidades, más un `ashenmarch_event` por cada asedio, dominio perdido o ganado, dominio a punto de desvanecerse y marcha registrada, para recibir avisos cuando atacan un territorio **cerca de tus zonas**.

**Varios jugadores por casa.** Cada jugador añade su propio token y tiene su propio dispositivo de Casa (`sensor.casa_cunningham_…`). Cada evento dice de quién es el reino.

## Instalación (HACS)

1. HACS → ⋮ → **Repositorios personalizados** → añade `https://github.com/cunningplay/ha-ashenmarch`, tipo **Integración**.
2. Instala **Ashenmarch** y reinicia Home Assistant.
3. Ajustes → Dispositivos y servicios → **Añadir integración** → **Ashenmarch**.

Requiere Home Assistant 2026.10 o posterior.

## Configuración

1. En [ashenmarch.com/portal/profile](https://ashenmarch.com/portal/profile), en **Tokens de acceso personal**, crea un token (llámalo "Home Assistant"). Empieza con `am_pat_` y se muestra **una sola vez**: cópialo.
2. Pégalo en la configuración de Ashenmarch en Home Assistant. La entrada lleva el nombre de tu Casa.
3. Cada jugador de la casa repite esto con su propio token. Añadir dos veces al mismo jugador no se permite.

El token es **de solo lectura**: lee tu propio reino y nada más. Revócalo en la misma página cuando quieras; Home Assistant pedirá uno nuevo solo para ese jugador.

## Entidades

Un dispositivo por jugador, con el nombre de su Casa. Dominios (con el área como atributo y el escudo como imagen), dominios bajo asedio, próximo en desvanecerse, posición (con `region` y `title`), distancia esta semana, última actualización; `binary_sensor` *Bajo asedio* (todo el reino) y *Bajo asedio cerca* (con el atributo `zones`); e `image` *Escudo de armas*, descargado por Home Assistant con tu token.

## Alertas cerca de tus zonas

Por defecto, las alertas solo llegan para territorios **a menos de 40 km (25 mi) de Casa**. Cámbialo en **Configurar**: *Cerca de mis zonas* o *Todos los territorios*; una o varias zonas; y el radio en tus unidades de Home Assistant. El radio por defecto es **40 km / 25 mi con una zona** y **8 km / 5 mi con dos o más**, y sigue al número de zonas hasta que escribas el tuyo. `march_filed` siempre llega.

## El evento `ashenmarch_event`

Los mismos campos que arriba: `type`, `message` (ya en el idioma del jugador), `message_with_zone` (*"… — cerca de Casa (5 km)"*), `player_id`, `house`, `device_id`, `zone`, `zone_name`, `distance`, `distance_unit`, `distance_km`, `zones_matched`, `territory`, `data`. Los ejemplos de automatización de arriba sirven igual.

## Privacidad

El token se guarda solo en Home Assistant, nunca aparece en los registros y se oculta en los diagnósticos. Las distancias a tus zonas se calculan localmente; las coordenadas de tus zonas no salen de Home Assistant.

## Licencia

MIT. Ver [LICENSE](LICENSE).
