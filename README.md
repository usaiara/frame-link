# Frame/Link

Protótipo acadêmico de câmera virtual para iPhone com jailbreak **rootless**. O tweak substitui os quadros de vídeo entregues por `mediaserverd` por imagens de uma cena do OBS. O código do tweak deriva de [EthanArbuckle/vcam-ios](https://github.com/EthanArbuckle/vcam-ios) e do [fork de axm535958](https://github.com/axm535958/vcam-ios). Este projeto acrescenta o painel Frame/Link para macOS, a ponte de transmissão e a limpeza do quadro remoto quando a conexão cai.

## Painel do Mac (Electron)

O aplicativo em `mac-app/` mostra a prévia ao vivo, detecta o iPhone por USB, lista os aplicativos instalados, troca a cena de programa do OBS, inicia ou para a ponte, instala ou remove o tweak e captura a tela do iPhone. O painel consulta o banco de pacotes do iPhone periodicamente: exibe **instalado**, **removido**, **desconectado** ou **não verificado** e habilita apenas a ação cabível. A seleção de um aplicativo serve para organizar o teste: **a substituição da câmera é global** e nenhum código é injetado nos apps.

O instalador macOS Apple Silicon e o pacote do tweak estão disponíveis nos lançamentos do projeto. Neste Mac, o aplicativo pronto está em `/Applications/Frame Link.app`. Para desenvolver ou gerar outro pacote:

```sh
cd mac-app
npm install
npm start
npm run dist
npm run dmg
```

O painel usa Python, Frida, FFmpeg e `idevicescreenshot` instalados no Mac. O iPhone deve estar desbloqueado, conectado por USB e com jailbreak/Frida ativos. Ao abrir o app, a ponte inicia automaticamente com a fonte de vídeo salva. O pacote `.deb` mais recente da pasta `packages/` é usado na instalação via USB. Na primeira execução local, as dependências Python podem ser instaladas com `python3 -m pip install --user -r requirements.txt`. O `.dmg` gerado é para macOS Apple Silicon e não tem assinatura Developer ID; em outro Mac, o sistema pode exigir aprovação manual na primeira abertura.

Na aba **Controle → Servidor OBS**, escolha **WebSocket** para controlar cenas do OBS no Mac ou em outro computador, ou **Fluxo RTMP** para receber uma transmissão já publicada por um servidor RTMP. Para um Windows na mesma rede, use o IP LAN dele; `127.0.0.1` sempre significa o próprio computador onde o endereço é usado. No modo RTMP, o Mac precisa ter FFmpeg instalado (`brew install ffmpeg`). O servidor RTMP precisa aceitar conexões de leitura de outros computadores, e a URL de leitura pode incluir uma chave de transmissão além do caminho do servidor. O Mac continua servindo `http://<IP-do-Mac>:8080/frame.jpg` ao iPhone nos dois modos.

Para enviar **diretamente do OBS do Windows ao Mac**, escolha **Receber RTMP do Windows neste Mac**. No Windows, abra **Configurações → Transmissão**, selecione o serviço **Personalizado** e copie do painel o **Servidor** (`rtmp://192.168.1.10:1935/camswap` nesta rede) e a **Chave de transmissão** (`live`). Clique em **Iniciar transmissão** no OBS. O Mac escuta na porta TCP 1935 e transforma os quadros recebidos em JPEG para o tweak do iPhone. Essa opção evita depender de um servidor RTMP no Windows.

## 1. OBS no Mac

Crie uma cena com sua webcam. Em **Ferramentas → Configurações do servidor WebSocket**, ative o servidor. A porta padrão é `4455`. No macOS, o programa lê a senha da configuração local do OBS; o endereço HTTP criado abaixo é o que o iPhone acessa.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python obs_stream_server.py
```

O programa captura a cena **ao vivo** em JPEG a 960 pixels, 12 FPS e qualidade 85. Para uma cena específica, acrescente `--source 'Nome da cena'`. Abra `http://127.0.0.1:8080/` no Mac: a página mostra a cena ao vivo. Neste Mac, o IP local encontrado foi `192.168.1.10`; confirme se ele continua igual antes de apresentar. Mac e iPhone precisam estar na mesma rede.

## 2. Tweak no iPhone

O pacote atual é para **iOS 15, arm64 e jailbreak rootless**, como o iPhone 6s Plus. Requer [Theos](https://theos.dev/docs/installation-macos), SDK iOS 16.5 e um carregador de tweaks no aparelho. Para compilar no Mac:

```sh
export THEOS="$HOME/theos"
./build.sh
```

Instale o arquivo `.deb` gerado em `packages/` pelo Sileo. O endereço do Mac está definido em `image_utils.m` para a demonstração:

```text
http://192.168.1.10:8080/frame.jpg
```

Se o IP do Mac mudar, altere essa constante e recompile o pacote. Abra esse mesmo endereço no Safari do iPhone antes de testar a câmera. O `mediaserverd` no iOS 15.8.8 não conseguiu ler o arquivo `/var/mobile/.vcam_url.txt`; por isso, este protótipo usa o endereço incluído no binário. Se o servidor parar ou um quadro falhar, o último quadro remoto é descartado e a câmera física volta a aparecer.

## Limites atuais

- Transmite **somente vídeo**. O microfone do iPhone continua sendo usado.
- 12 FPS melhora a fluidez da prova de conceito, mas ainda não equivale a uma câmera de 30 FPS.
- O hook em `mediaserverd` é experimental e pode variar entre versões do iOS. Teste primeiro no app Câmera; Discord ainda precisa de teste no aparelho.
- O servidor HTTP não tem autenticação; use uma rede local confiável e encerre o processo após a demonstração.
- O código herdado contém suporte incompleto a vídeo local. A demonstração principal usa os quadros do OBS.
