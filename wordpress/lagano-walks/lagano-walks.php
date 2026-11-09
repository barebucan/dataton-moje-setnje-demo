<?php
/**
 * Plugin Name: Moje šetnje
 * Description: Planer pješačkih ruta iz otvorenih podataka za Zagreb.
 * Version: 0.7.1
 * License: GPL-2.0-or-later
 */
if (!defined('ABSPATH')) exit;
add_filter('document_title_parts', function ($parts) {
 $path=trim((string)wp_parse_url($_SERVER['REQUEST_URI'] ?? '/',PHP_URL_PATH),'/');
 if (is_front_page() || $path==='app') $parts['title'] = $path==='app' ? 'Planer šetnje · Moje šetnje' : 'Moje šetnje';
 return $parts;
});
add_action('rest_api_init', function() {
 foreach (['scene','search','health'] as $path) {
  register_rest_route('lagano/v1', '/'.$path, ['methods'=>'GET','permission_callback'=>'__return_true','callback'=>function($r) use($path) {
   $url=LAGANO_BACKEND.'/'.$path;
   if ($path==='search') $url.='?q='.rawurlencode(mb_substr(sanitize_text_field($r->get_param('q') ?? ''),0,120));
   $result=wp_remote_get($url,['timeout'=>15]);
   if (is_wp_error($result)) return new WP_Error('lagano_unavailable','Planer se trenutačno ne može povezati. Pokušaj ponovno.', ['status'=>503]);
   $data=json_decode(wp_remote_retrieve_body($result),true);
   if (!is_array($data)) return new WP_Error('lagano_response','Podaci se trenutačno ne mogu učitati.', ['status'=>502]);
   return new WP_REST_Response($data,wp_remote_retrieve_response_code($result));
  }]);
 }
 foreach (['plan','gpx'] as $path) {
  register_rest_route('lagano/v1','/'.$path,['methods'=>'POST','permission_callback'=>'__return_true','callback'=>function($r) use($path) {
   if (strlen($r->get_body())>800000) return new WP_Error('lagano_size','Zahtjev je prevelik.', ['status'=>413]);
   $body=$r->get_json_params();
   if (!is_array($body)) return new WP_Error('lagano_input','Provjeri unesene podatke.', ['status'=>400]);
   $result=wp_remote_post(LAGANO_BACKEND.'/'.$path,['timeout'=>70,'headers'=>['Content-Type'=>'application/json'],'body'=>wp_json_encode($body)]);
   if (is_wp_error($result)) return new WP_Error('lagano_unavailable','Izračun se nije dovršio. Pokušaj ponovno ili odaberi drugo polazište.', ['status'=>503]);
   $data=json_decode(wp_remote_retrieve_body($result),true);
   if (!is_array($data)) return new WP_Error('lagano_response','Izračun se nije mogao prikazati. Pokušaj ponovno.', ['status'=>502]);
   return new WP_REST_Response($data,wp_remote_retrieve_response_code($result));
  }]);
 }
});
add_action('wp_enqueue_scripts', function() {
 $base=plugin_dir_url(__FILE__).'assets/';
 $path=trim((string)wp_parse_url($_SERVER['REQUEST_URI'] ?? '/',PHP_URL_PATH),'/');
 $is_app=$path==='app';
 wp_enqueue_style('lagano-leaflet',$base.'leaflet.css',[], '1.9.4');
 wp_enqueue_style('lagano-maplibre',$base.'vendor/maplibre-gl.css',['lagano-leaflet'],'5.15.0');
 wp_enqueue_style('lagano-app',$base.'app.css',['lagano-leaflet','lagano-maplibre'],filemtime(__DIR__.'/assets/app.css'));
 wp_enqueue_script('lagano-leaflet',$base.'leaflet.js',[],'1.9.4',true);
 wp_enqueue_script('lagano-maplibre',$base.'vendor/maplibre-gl.js',[],'5.15.0',true);
 wp_enqueue_script('lagano-maplibre-leaflet',$base.'vendor/leaflet-maplibre-gl.js',['lagano-leaflet','lagano-maplibre'],'0.1.4',true);
 if ($is_app) {
  wp_enqueue_script('lagano-app',$base.'app.js',['lagano-maplibre-leaflet'],filemtime(__DIR__.'/assets/app.js'),true);
  wp_add_inline_script('lagano-app','window.LAGANO='.wp_json_encode(['api'=>esc_url_raw(rest_url('lagano/v1/')),'download'=>esc_url_raw(admin_url('admin-post.php'))]).';','before');
 } else {
  wp_enqueue_style('lagano-cinematic',$base.'cinematic-demo.css',['lagano-app'],filemtime(__DIR__.'/assets/cinematic-demo.css'));
  wp_enqueue_script('lagano-landing',$base.'landing-page.js',['lagano-maplibre-leaflet'],filemtime(__DIR__.'/assets/landing-page.js'),true);
  wp_add_inline_script('lagano-landing','window.LAGANO='.wp_json_encode(['api'=>esc_url_raw(rest_url('lagano/v1/'))]).';','before');
  wp_enqueue_script('lagano-cinematic',$base.'cinematic-demo.js',['lagano-landing'],filemtime(__DIR__.'/assets/cinematic-demo.js'),true);
 }
});
add_shortcode('lagano_walks', function($atts=[]) {
 $atts=shortcode_atts(['view'=>'landing'],$atts,'lagano_walks');
 $is_app=$atts['view']==='app';
 ob_start(); ?>
<?php if ($is_app): ?>
<a class="skip-link" href="#planner">Preskoči na planer</a>
<?php endif; ?>
<header class="site-header">
 <a class="brand" href="<?php echo esc_url(home_url('/')); ?>" aria-label="Moje šetnje — početna"><span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="M10 24c-6-5-5-13 1-17 2 5 8 5 9 10 1 4-3 8-7 7" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/><path d="M12 24c1-6 5-10 12-15" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg></span>Moje šetnje<span class="brand-dot">.</span></a>
 <span class="header-location"><span class="live-dot"></span>Zagreb, tvojim korakom</span>
 <nav aria-label="Glavna navigacija"><?php if ($is_app): ?><a href="<?php echo esc_url(home_url('/')); ?>" class="guide-link">O aplikaciji</a><?php else: ?><a href="#kako-radi" class="guide-link">Kako radi</a><?php endif; ?><details class="header-menu"><summary>Pomoć i izvori</summary><div><button class="text-button" data-dialog="how-dialog">Kako biramo rute</button><button class="text-button" data-dialog="sources-dialog">Izvori podataka</button></div></details><?php if (!$is_app): ?><a href="<?php echo esc_url(home_url('/app/')); ?>" class="app-cta">Isprobaj aplikaciju</a><?php endif; ?></nav>
</header>
<?php if (!$is_app): ?>
<div class="landing" id="pocetna">
 <section class="walk-intro" aria-labelledby="landing-title">
  <div><p class="landing-kicker">ŠETNJA PO TVOJEM DANU</p><h1 id="landing-title">Voliš hodati. Samo ne želiš opet istim putem.</h1><p>Reci koliko vremena imaš i što želiš vidjeti. Pronaći ćemo kružnu šetnju koja stane u tvoj dan.</p></div>
  <div class="walk-intro-visual" role="img" aria-label="Ilustracija šetnje kroz Zrinjevac"></div>
 </section>
 <section class="walk-choice" aria-labelledby="choice-title">
  <p class="landing-kicker">DANAS</p><h2 id="choice-title">Imam <strong>30 minuta</strong>, zanima me <strong>kultura</strong> i krećem sa <strong>Zrinjevca</strong>.</h2>
  <button type="button" class="find-example" id="find-example" aria-controls="route-example" aria-expanded="false">Nađi mi šetnju <span aria-hidden="true">→</span></button>
 </section>
 <section class="route-example" id="route-example" aria-labelledby="example-title" hidden>
  <div class="example-map-wrap"><div id="example-map" role="img" aria-label="Primjer unaprijed izračunate kružne šetnje sa Zrinjevca"></div><span class="example-start">Zrinjevac · početak i povratak</span></div>
  <div class="example-copy"><p class="landing-kicker">PRIMJER STVARNE RUTE</p><h2 id="example-title">Tvoja šetnja sa Zrinjevca.</h2><p class="example-stats">oko 29 min <span>·</span> 2,4 km <span>·</span> kružna ruta</p><p>Planer najprije zadržava pješačke krugove koji stanu u tvoje vrijeme, zatim uspoređuje što se nalazi uz trasu i predlaže kvalitetan povratak na početak.</p><a href="<?php echo esc_url(home_url('/app/?preset=culture30')); ?>">Složi svoju rutu u aplikaciji →</a></div>
 </section>
</div>
<?php endif; ?>
<?php if ($is_app): ?>
<main class="app-layout" id="planner">
 <section class="planner-panel" aria-labelledby="planner-title">
  <div class="panel-intro"><h1 id="planner-title" tabindex="-1">Vrijeme za <span>šetnju.</span></h1><p>Odaberi polazište i trajanje.</p></div>
  <form id="walk-form" novalidate><fieldset class="mode-switch"><legend class="sr-only">Oblik rute</legend><label><input type="radio" name="mode" value="loop" checked><span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M15 5a7 7 0 1 0 1 9M15 1v4h-4"/></svg>Kružna šetnja</span></label><label><input type="radio" name="mode" value="destination"><span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 16V5a2 2 0 1 1 4 0v8a2 2 0 1 0 4 0V4m-3 3 3-3 3 3"/></svg>Do odredišta</span></label></fieldset>

   <div class="field-block"><div class="field-heading"><label for="start-search">Polazište</label><button type="button" class="inline-button" id="pick-start">Odaberi na karti</button></div><div class="search-wrap"><span class="input-icon start-dot" aria-hidden="true"></span><input id="start-search" class="place-input" type="text" value="Zrinjevac" autocomplete="off" role="combobox" aria-expanded="false" aria-autocomplete="list" aria-controls="start-options" placeholder="Mjesto ili adresa u Zagrebu"><button type="button" class="location-button" id="geolocate" aria-label="Koristi moju lokaciju" title="Koristi moju lokaciju"><svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="5"/><path d="M10 1v4m0 10v4M1 10h4m10 0h4"/></svg></button><ul class="suggestions" id="start-options" role="listbox" hidden></ul></div><p class="field-hint" id="start-hint">Javni primjer · možeš promijeniti polazište.</p></div>
   <div class="field-block" id="destination-field" hidden><div class="field-heading"><label for="end-search">Kamo idemo?</label><button type="button" class="inline-button" id="pick-end">Odaberi na karti</button></div><div class="search-wrap"><span class="input-icon end-dot" aria-hidden="true"></span><input id="end-search" class="place-input" type="text" autocomplete="off" role="combobox" aria-expanded="false" aria-autocomplete="list" aria-controls="end-options" placeholder="Odaberi odredište"><ul class="suggestions" id="end-options" role="listbox" hidden></ul></div></div>
   <fieldset class="field-block" id="duration-field"><legend>Trajanje</legend><div class="duration-options"><label><input type="radio" name="duration" value="20"><span>20 <small>min</small></span></label><label><input type="radio" name="duration" value="30" checked><span>30 <small>min</small></span></label><label><input type="radio" name="duration" value="45"><span>45 <small>min</small></span></label><label><input type="radio" name="duration" value="60"><span>60 <small>min</small></span></label></div></fieldset>

<fieldset class="field-block preference-field"><legend>Vrsta šetnje</legend><div class="preference-options"><label><input type="radio" name="preference" value="balanced" checked><span><span class="preference-icon" aria-hidden="true">☀</span>Uravnoteženo</span></label><label><input type="radio" name="preference" value="green"><span><span class="preference-icon leaf-icon" aria-hidden="true">♧</span>Zelenilo</span></label><label><input type="radio" name="preference" value="pedestrian"><span><span class="preference-icon" aria-hidden="true">↝</span>Pješački putovi</span></label><label><input type="radio" name="preference" value="culture"><span><span class="preference-icon" aria-hidden="true">⌖</span>Kultura</span></label></div></fieldset>   <button type="submit" class="primary-button" id="plan-button"><span id="plan-label">Pronađi šetnju</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10h12m-5-5 5 5-5 5"/></svg></button>
   <p class="form-note" id="form-note">Vraćaš se na polazište.</p>
   <div id="planner-status" class="status-message" role="status" aria-live="polite"></div><div id="no-route-actions" class="no-route-actions" hidden><button type="button" id="retry-shorter" class="secondary-button" hidden>Pokušaj kraći krug</button><button type="button" id="retry-duration" class="secondary-button">Pokušaj dulju šetnju</button><button type="button" id="retry-origin" class="inline-button">Odaberi drugo polazište na karti</button></div><button type="button" id="retry-init" class="secondary-button" hidden>Ponovno učitaj kartu</button>
   <div class="update-feedback"><p id="filter-result" class="filter-feedback" role="status" hidden></p><button type="button" id="jump-results" class="inline-button jump-results" hidden>Pogledaj ažuriranu šetnju ↓</button></div>
  </form>
  <section class="results-section" id="results-section" aria-labelledby="results-title" hidden><div class="results-heading"><div><h2 id="results-title" tabindex="-1">Odaberi rutu</h2></div><span id="result-count" class="count-badge"></span></div><div class="result-actions"><button type="button" class="inline-button" id="edit-choices">Polazište i vrijeme</button><button type="button" class="inline-button mobile-map-link" id="show-map">Prikaži kartu ↓</button></div><p id="route-request" class="route-request"></p><fieldset class="result-types"><legend>Vrsta šetnje</legend><div class="result-type-options"><button type="button" data-preference="balanced" aria-pressed="true">Uravnoteženo</button><button type="button" data-preference="green" aria-pressed="false">Zelenilo</button><button type="button" data-preference="pedestrian" aria-pressed="false">Pješački putovi</button><button type="button" data-preference="culture" aria-pressed="false">Kultura</button></div></fieldset><p id="route-choice-status" class="route-choice-status" role="status" aria-live="polite"></p><div id="route-options" class="route-options" role="radiogroup" aria-label="Predložene šetnje"></div><div id="route-detail" class="route-detail"></div></section>
  <dialog id="voice-coverage-dialog" class="info-dialog voice-coverage-dialog" aria-labelledby="voice-coverage-title"><div class="dialog-heading"><span class="eyebrow">GLASOVNI VODIČ · DEMO</span><button type="button" class="dialog-close" id="close-voice-coverage" aria-label="Zatvori">&times;</button></div><h2 id="voice-coverage-title">Ovaj dio još nije mapiran.</h2><p>Za ovu rutu još nemamo pripremljene priče koje vrijedi slušati. Isprobaj jednu od dviju ruta na kojima je svaka znamenitost ručno odabrana i provjerena.</p><div class="mapped-demo-routes"><a class="mapped-demo-route" data-demo-route="green-horseshoe" href="/voice/?demo=green-horseshoe"><strong>Zelena potkova</strong><span>HNK do Umjetničkog paviljona · 8 priča · oko 15 min</span></a><a class="mapped-demo-route" data-demo-route="theatres-tesla" href="/voice/?demo=theatres-tesla"><strong>Kazališta, arhitektura i Tesla</strong><span>Kružna ruta · 4 priče · oko 13 min</span></a></div><p class="privacy-note">U glasovni demo ne puštamo kataloške opise ni mjesta za koja još nemamo smislenu priču.</p></dialog>
  <div class="panel-footer"><span class="small-leaf" aria-hidden="true">↟</span><p>Stvarni putovi. Otvoreni podaci.<br><button class="inline-button" data-dialog="how-dialog">Saznaj kako biramo rute</button></p></div>
 </section>
<section class="map-panel" aria-label="Karta šetnje kroz Zagreb"><div id="walk-map" role="region" aria-label="Interaktivna karta; polazište je moguće odabrati i u obrascu"></div><div class="map-topbar"><span class="coverage-pill"><span class="live-dot"></span>Zagreb <span class="pilot-label">PILOT</span></span><div class="map-tools"><button type="button" class="map-control expand-control" id="expand-map" aria-label="Otvori veliku kartu" aria-expanded="false" title="Otvori veliku kartu"><span aria-hidden="true">⛶</span><span class="expand-label">Velika karta</span></button><button type="button" class="map-control" id="fit-map" aria-label="Prilagodi prikaz ruti" title="Prilagodi prikaz ruti"><svg viewBox="0 0 20 20"><path d="M7 3H3v4m10-4h4v4M3 13v4h4m10-4v4h-4"/></svg></button></div></div><div class="map-help" id="map-help">Kotačićem zumiraj. Klikni na kartu za polazište.</div><div class="map-feedback" id="map-status" role="status" hidden></div><button type="button" id="back-to-planner" class="map-back-button" hidden>← Natrag na planer</button><div class="map-pick-message" id="map-pick-message" role="status" hidden>Klikni na kartu i odaberi polazište.<button id="cancel-pick" type="button">Odustani</button></div><details class="map-layers" aria-label="Slojevi prikaza karte; ne mijenjaju izračun rute"><summary>Slojevi karte</summary><div class="layer-options"><label><input id="layer-green" type="checkbox" checked><span class="legend-dot green-dot"></span>Zelene površine</label><label><input id="layer-pedestrian" type="checkbox" checked><span class="legend-dot pedestrian-dot"></span>Gradske zone</label></div><p>Slojevi mijenjaju prikaz, ne izračun rute.</p></details><div class="calculation-story" id="calculation-story" hidden><strong>Tražimo dobar krug, ne samo najkraći.</strong><ol><li>Povezujemo polazište s pješačkim putovima</li><li>Uspoređujemo rute koje stanu u tvoje vrijeme</li><li>Provjeravamo što se nalazi uz trasu</li><li>Pripremamo najbolji dostupni prijedlog</li></ol></div><div class="map-welcome" id="map-welcome"><span class="welcome-icon" aria-hidden="true">↝</span><div><strong>Grad izgleda drukčije kad usporiš.</strong><p>Odaberi šetnju i istraži put koji ti odgovara.</p></div></div><div class="map-route-summary" id="map-route-summary" hidden></div><nav class="mobile-view-switch" aria-label="Prikaz šetnje"><button type="button" id="mobile-route" aria-pressed="true"><span aria-hidden="true">☷</span> <span id="mobile-route-label">Planer</span></button><button type="button" id="mobile-map" aria-pressed="false"><span aria-hidden="true">⌖</span> Karta</button></nav></section>
</main>
<?php else: ?>
<section class="guide-explainer" id="kako-radi" aria-labelledby="guide-title">
 <div class="guide-copy"><p class="landing-kicker">RUTA JE ODABRANA</p><h2 id="guide-title">Stavi slušalice. Mobitel u džep. I kreni.</h2><p>Dok hodaš, GPS vodiču govori gdje se nalaziš i čemu se približavaš. Kad dođeš blizu relevantnog mjesta, AI vodič započinje kratku priču na hrvatskom. Možeš ga prekinuti i pitati nešto što upravo vidiš.</p></div>
 <div class="public-data-story"><p>Rutu povezujemo s javno dostupnim podacima <strong>Grada Zagreba</strong> i portala <strong>data.gov.hr</strong> te s otvorenom pješačkom mrežom <strong>OpenStreetMapa</strong>. Tako javni zapisi postaju priča vezana uz stvarno mjesto i trenutak šetnje.</p><p class="data-flow" aria-label="Od javnih podataka do razgovora"><span>javni podaci</span><i aria-hidden="true">→</i><span>mjesta uz rutu</span><i aria-hidden="true">→</i><span>GPS kontekst</span><i aria-hidden="true">→</i><span>hrvatski razgovor</span></p><small>Podaci opisuju evidentirana mjesta i putove; ne potvrđuju današnje radove, radno vrijeme ili prohodnost.</small></div>
</section>
<section class="cinematic-section" aria-labelledby="demo-title">
 <div class="cinematic-section-heading"><h2 id="demo-title">Ovako to izgleda u hodu.</h2></div>
 <div class="cinematic-demo-wrap"><div id="cinematic-demo" data-phase="idle"><div class="cinematic-stage"><div class="cinematic-map-viewport"><div class="cinematic-map-scene"><div id="cinematic-map" class="cinematic-map" role="img" aria-label="Kretanje vodiča od HNK-a preko Zrinjevca do Umjetničkog paviljona"></div></div></div><p class="cinematic-street">Sada: <span data-demo-street>Trg Republike Hrvatske</span></p><article class="cinematic-landmark-card"><div class="cinematic-card-image is-hnk" data-demo-image role="img" aria-label="Ilustracija: HNK i Zdenac života"></div><div class="cinematic-card-copy"><p class="cinematic-card-kicker" data-demo-kicker></p><h3 data-demo-title></h3><p data-demo-copy></p></div></article><p class="cinematic-user-bubble"><strong>Ti:</strong> Čemu je služio brojčanik?</p><p class="cinematic-guide-bubble"><strong>Vodič:</strong> Imao je brojčanik s 24 sata i služio je za usklađivanje ručnih satova.</p></div></div></div>
</section>
<section class="final-try" aria-labelledby="final-try-title"><div><p class="landing-kicker">ODABERI PA KRENI</p><h2 id="final-try-title">Želiš probati cijelu aplikaciju?</h2><p>Najprije odaberi rutu u planeru. Zatim ćemo baš tu trasu i mjesta uz nju pripremiti za razgovor s vodičem.</p></div><a href="<?php echo esc_url(home_url('/app/')); ?>">Otvori aplikaciju <span aria-hidden="true">→</span></a></section>
<?php endif; ?>
<dialog id="how-dialog" class="info-dialog" aria-labelledby="how-title"><div class="dialog-heading"><span class="eyebrow">JEDNOSTAVNO, KORAK PO KORAK</span><button class="dialog-close" aria-label="Zatvori" type="button">×</button></div><h2 id="how-title">Kako nastaje tvoja šetnja?</h2><ol class="how-list"><li><span>1</span><div><h3>Ti odabereš početak i vrijeme.</h3><p>Koristi javni primjer, potraži adresu u Zagrebu i okolici ili postavi točku na karti.</p></div></li><li><span>2</span><div><h3>Mi usporedimo stvarne putove.</h3><p>Pješačka mreža povezuje javno evidentirane zone i sadržaje. Tvoj prioritet utječe na izbor, a ne izmišlja kraće vrijeme hodanja.</p></div></li><li><span>3</span><div><h3>Ti vidiš razliku i biraš.</h3><p>Duljina, procijenjeno trajanje i razlozi dostupni su uz svaku rutu. Možeš preuzeti GPX za svoj uređaj.</p></div></li></ol><div class="info-callout"><strong>Plan za šetnju, uz provjeru na licu mjesta.</strong><p>Vrijeme je procjena hodanja bez stajanja. Podaci ne potvrđuju današnju prohodnost, radno vrijeme sadržaja ili pristupačnost za invalidska kolica. Zelenilo nije jamstvo hlada. Poštuj oznake na terenu.</p></div><p class="privacy-note">Planer ne traži račun. Pretraga mjesta ostaje na našem serveru; upiti se ne šalju vanjskom geokoderu. Kartografsku podlogu učitava vanjski pružatelj, uz atribuciju na karti.</p></dialog>
<dialog id="sources-dialog" class="info-dialog" aria-labelledby="sources-title"><div class="dialog-heading"><span class="eyebrow">OD PODATKA DO TVOG PUTA</span><button class="dialog-close" aria-label="Zatvori" type="button">×</button></div><h2 id="sources-title">Izvori kojima se služimo</h2><p>Javni slojevi određuju obilježja i moguće stanice šetnje. Mreža putova povezuje ih u rutu.</p><div id="source-list" class="source-list"></div><p class="privacy-note">Prototip za Datathon · presjek preuzet 27. listopada 2026. Datum preuzimanja nije potvrda da je svaki zapis tada provjeren. Lagano je projekt tima, bez tvrdnje o službenoj podršci izdavača.</p></dialog>
<?php return ob_get_clean();
});

function lagano_download_gpx() {
 $raw=wp_unslash($_POST['coordinates'] ?? '');
 if (!is_string($raw) || strlen($raw)>800000) wp_die('Zahtjev za rutu nije valjan.', 'Lagano', ['response'=>400]);
 $coordinates=json_decode($raw,true);
 if (!is_array($coordinates)) wp_die('Nedostaju koordinate rute.', 'Lagano', ['response'=>400]);
 $result=wp_remote_post(LAGANO_BACKEND.'/gpx',['timeout'=>25,'headers'=>['Content-Type'=>'application/json'],'body'=>wp_json_encode(['name'=>sanitize_text_field(wp_unslash($_POST['name'] ?? 'Moja šetnja')),'coordinates'=>$coordinates])]);
 if (is_wp_error($result)) wp_die('GPX se trenutačno ne može pripremiti. Vrati se na planer i pokušaj ponovno.', 'Lagano', ['response'=>503]);
 $data=json_decode(wp_remote_retrieve_body($result),true);
 if (wp_remote_retrieve_response_code($result)!==200 || empty($data['gpx'])) wp_die('Ruta nije valjana za izvoz.', 'Lagano', ['response'=>422]);
 nocache_headers();
 header('Content-Type: application/gpx+xml; charset=utf-8');
 header('Content-Disposition: attachment; filename="lagano-setnja.gpx"');
 header('X-Content-Type-Options: nosniff');
 echo $data['gpx'];
 exit;
}
add_action('admin_post_nopriv_lagano_gpx','lagano_download_gpx');
add_action('admin_post_lagano_gpx','lagano_download_gpx');
