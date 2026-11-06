package org.forge.answer
import android.app.Activity
import android.os.Bundle
import android.widget.TextView
fun answer()=42
class MainActivity:Activity(){override fun onCreate(state:Bundle?){super.onCreate(state);setContentView(TextView(this).apply{text=answer().toString()})}}
