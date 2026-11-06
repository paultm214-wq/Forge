plugins { id("com.android.application");kotlin("android") }
android { namespace="org.forge.answer";compileSdk=35
 defaultConfig { applicationId="org.forge.answer"; minSdk=23; targetSdk=35;versionCode=1;versionName="1.0" }
 compileOptions { sourceCompatibility=JavaVersion.VERSION_17;targetCompatibility=JavaVersion.VERSION_17 }
 kotlinOptions { jvmTarget="17" } }
dependencies { testImplementation("junit:junit:4.13.2") }
