# Aviso de sismos por WhatsApp

Avisa cuando se publica un sismo de magnitud **4.5 o más**, a 300 km de Medellín o en Colombia. El mensaje sale al publicarse el dato, no antes de que llegue la onda.

Haz las partes en este orden. n8n va primero porque Dokploy llama una dirección que solo existe cuando el workflow ya está publicado.

Antes de los dos, inventa una clave y anótala. Ejemplo: `sismo-medellin-2026-k7m`. No se descarga de ningún sitio. Es la misma en n8n y en Dokploy. Sirve para que nadie más dispare el WhatsApp si conoce la URL.

## 1. n8n

Aquí se recibe el sismo y se manda el WhatsApp.

1. En n8n, menú de workflows, **Import from file** y elige `avisar-sismo-whatsapp.json`.
2. Abre el nodo **Token correcto**. En el valor de la derecha borra `CAMBIA-ESTE-TOKEN` y pega la clave que inventaste.
3. Abre el nodo **Avisar por WhatsApp**. En **Header Auth** elige la credencial **EvolutionApi** (la misma del aviso de correo).
4. Publica el workflow. Sin esto la dirección no responde.
5. La dirección que usará Dokploy es `https://n8n.capitalimpulso.com/webhook/sismo-alerta`.

## 2. Dokploy

n8n no puede quedarse escuchando los sismos. Este servicio corre en el servidor, recibe el dato de EMSC o USGS y llama la dirección del paso anterior.

Dokploy no ve la carpeta de tu computador. Clona el repositorio y busca el compose en la raíz.

1. En el proyecto donde ya está n8n, **Create Service** → **Compose**.
2. Nombre: `sismo-listener`.
3. Conecta el repositorio `mendozalz/workflow-notificaciones-personales`. El archivo de compose se deja en `./docker-compose.yml`, que es el valor por defecto.
4. En **Environment** deja estas variables. `ALERT_TOKEN` es la misma clave del nodo **Token correcto**.

```text
N8N_WEBHOOK_URL=https://n8n.capitalimpulso.com/webhook/sismo-alerta
ALERT_TOKEN=la-clave-que-inventaste
WHATSAPP_NUMBERS=573192754132
MIN_MAG=4.5
```

5. Para más celulares, sepáralos con coma, sin `+` y sin espacios: `573192754132,573001112233`. El que envía es la instancia **Mendozalz**. No pongas el número de esa misma instancia: a sí mismo no llega como aviso normal.
6. **Deploy**.
7. En **Logs** del contenedor `sismo-listener` debe aparecer `EMSC conectado`. Si aparece `401`, la clave de Dokploy y la de n8n no son iguales.

Cloudflare ya tiene el dominio `n8n.capitalimpulso.com`. El webhook es una ruta de n8n, no un dominio nuevo. En Cloudflare no se crea registro DNS, túnel ni regla.

## Probar

Hazlo en este orden. El paso 1 no manda WhatsApp. El paso 2 sí.

Cada aviso lleva **un solo celular** en `number`. La coma de `WHATSAPP_NUMBERS` es solo para Dokploy: el listener hace un POST por cada número. Evolution rechaza `573192754132,573022408297` en un mismo campo.

1. Token malo. Debe responder `401` y `{"error":"unauthorized"}`.

```bash
curl -sS -D - -X POST https://n8n.capitalimpulso.com/webhook/sismo-alerta \
  -H 'Content-Type: application/json' \
  -H 'X-Alert-Token: clave-mala' \
  -d '{"text":"prueba","number":"573192754132"}'
```

2. Token bueno, el mismo del nodo **Token correcto**. Un comando por celular. Cada uno debe responder `200` y `{"ok":true}`, y llegar ese WhatsApp.

```bash
curl -sS -D - -X POST https://n8n.capitalimpulso.com/webhook/sismo-alerta \
  -H 'Content-Type: application/json' \
  -H 'X-Alert-Token: la-clave-que-inventaste' \
  -d '{"text":"Prueba de aviso de sismo. Ignorar.","number":"573192754132"}'

curl -sS -D - -X POST https://n8n.capitalimpulso.com/webhook/sismo-alerta \
  -H 'Content-Type: application/json' \
  -H 'X-Alert-Token: la-clave-que-inventaste' \
  -d '{"text":"Prueba de aviso de sismo. Ignorar.","number":"573022408297"}'
```

Si el paso 1 da `404`, el workflow no está publicado. La URL de prueba del editor (`/webhook-test/...`) no es la que usa el listener.

Si el paso 2 responde `502` con el texto `error code: 502` y cabecera `server: cloudflare`, el token sí pasó. n8n contestó error porque Evolution no aceptó el mensaje, y Cloudflare sustituyó el cuerpo. En la ejecución, la línea verde sale de **Avisar por WhatsApp** hacia **Fallo de WhatsApp**. Abre ese nodo y lee el error de Evolution. Lo más común es haber puesto los dos celulares en un solo `number`.

3. Después del **Deploy** en Dokploy, abre **Logs**. Tiene que salir `EMSC conectado`. Un `401` ahí es la misma clave distinta en n8n y en `ALERT_TOKEN`.

El listener no se puede forzar con un sismo falso desde Dokploy. Cuando EMSC o USGS publiquen uno de magnitud 4.5 o más, en Colombia o a 300 km de Medellín, el WhatsApp sale solo. El mismo evento no se repite salvo que la magnitud suba 0.3 o más.

## Qué llega al WhatsApp

Magnitud, lugar, distancia a Medellín, profundidad, hora de Bogotá y si la onda todavía puede ir en camino. El mismo sismo solo se repite si la magnitud sube 0.3 o más.
