plugins { kotlin("jvm") version "2.2.21"; application }
repositories { mavenCentral() }
dependencies { testImplementation("org.junit.jupiter:junit-jupiter:5.11.4") }
tasks.test { useJUnitPlatform() }
application { mainClass.set("MainKt") }
kotlin { jvmToolchain(21) }
